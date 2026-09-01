"""The OSB layer: translates Open Service Broker API calls into admin calls.

This file is the only place that knows the OSB API and its rules:
which errors mean 400/409/410, what idempotency requires, and what is
stored between calls.  It contains NO SQL - all database work is delegated
to GaussDBAdmin, all memory to StateStore, all offering definitions to
plans.py/params.py.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from openbrokerapi import errors
from openbrokerapi.service_broker import (
    BindDetails,
    Binding,
    BindState,
    DeprovisionDetails,
    DeprovisionServiceSpec,
    GetBindingSpec,
    GetInstanceDetailsSpec,
    LastOperation,
    OperationState,
    ProvisionDetails,
    ProvisionedServiceSpec,
    ProvisionState,
    ServiceBroker,
    UnbindDetails,
    UnbindSpec,
    UpdateDetails,
    UpdateServiceSpec,
)

from .config import Settings
from .gaussdb import AlreadyExistsError, GaussDBAdmin, names_for, user_for
from .params import InstanceParams, resolve_binding_params, resolve_instance_params
from .plans import SERVICE_ID, PlanSpec, build_service, load_plans
from .state import StateStore

logger = logging.getLogger(__name__)


class GaussDbBroker(ServiceBroker):
    def __init__(self, admin: GaussDBAdmin, store: StateStore, settings: Settings):
        self._admin = admin
        self._store = store
        self._settings = settings
        # The plan catalog is data; load it once, fail fast on a bad file.
        self._plans = load_plans(settings.plans_file)
        self._plan_index = {plan.id: plan for plan in self._plans}

    # -- helpers -------------------------------------------------------------

    def _names(self, instance_id: str):
        return names_for(instance_id, self._settings.name_prefix)

    def _get_plan(self, plan_id: str) -> PlanSpec:
        plan = self._plan_index.get(plan_id)
        if plan is None:
            raise ValueError(f"Unknown plan_id {plan_id!r}")
        return plan

    @staticmethod
    def _require_service(details) -> None:
        if details.service_id != SERVICE_ID:
            raise errors.ErrInvalidParameters(f"unknown service_id {details.service_id!r}")

    def _credentials(self, database: str, username: str, password: str) -> dict:
        s = self._settings
        userinfo = quote(username, safe="") + ":" + quote(password, safe="")
        uri = f"postgresql://{userinfo}@{s.db_host}:{s.db_port}/{database}"
        if s.db_sslmode != "disable":
            uri += f"?sslmode={quote(s.db_sslmode, safe='')}"
        return {
            "uri": uri,
            "hostname": s.db_host,
            "port": s.db_port,
            "database": database,
            "username": username,
            "password": password,
            "sslmode": s.db_sslmode,
            "jdbcUrl": f"jdbc:postgresql://{s.db_host}:{s.db_port}/{database}",
        }

    # -- catalog -------------------------------------------------------------

    def catalog(self):
        return build_service(self._plans, self._settings.tablespaces)

    # -- instance lifecycle ----------------------------------------------------

    def provision(
        self, instance_id: str, details: ProvisionDetails, async_allowed: bool, **kwargs
    ) -> ProvisionedServiceSpec:
        self._require_service(details)
        try:
            plan = self._get_plan(details.plan_id)
            spec = resolve_instance_params(plan, details.parameters, self._settings.tablespaces)
        except ValueError as exc:
            raise errors.ErrInvalidParameters(str(exc)) from exc

        if self._settings.storage_mode == "tablespace" and spec.tablespace:
            raise errors.ErrInvalidParameters(
                "'tablespace' cannot be set in tablespace storage mode"
                " (each instance already gets a dedicated quota-capped tablespace)"
            )

        existing = self._store.get_instance(instance_id)
        if existing is not None:
            if existing["plan_id"] == spec.plan_id and json.loads(existing["params_json"]) == spec.as_dict():
                return ProvisionedServiceSpec(state=ProvisionState.IDENTICAL_ALREADY_EXISTS)
            raise errors.ErrInstanceAlreadyExists()

        names = self._names(instance_id)
        logger.info("Provisioning logical database %s (plan %s)", names.database, plan.id)
        try:
            self._admin.provision(names, spec, self._settings.storage_mode)
        except AlreadyExistsError:
            raise errors.ErrInstanceAlreadyExists() from None
        self._store.put_instance(instance_id, SERVICE_ID, spec.plan_id, names.database, spec.as_dict())
        return ProvisionedServiceSpec()

    def update(
        self, instance_id: str, details: UpdateDetails, async_allowed: bool, **kwargs
    ) -> UpdateServiceSpec:
        self._require_service(details)
        existing = self._store.get_instance(instance_id)
        if existing is None:
            raise errors.ErrInstanceDoesNotExist()

        # Plan changes are advertised as unsupported in the catalog.
        previous_plan = details.previous_values.plan_id if details.previous_values else existing["plan_id"]
        target_plan = details.plan_id or previous_plan
        if target_plan != previous_plan:
            raise errors.ErrPlanChangeNotSupported()

        try:
            spec = resolve_instance_params(
                self._get_plan(previous_plan), details.parameters, self._settings.tablespaces
            )
        except ValueError as exc:
            raise errors.ErrInvalidParameters(str(exc)) from exc

        names = self._names(instance_id)
        logger.info("Updating logical database %s", names.database)
        self._admin.update(names, spec, self._settings.storage_mode)
        self._store.update_instance_params(instance_id, spec.as_dict())
        return UpdateServiceSpec(is_async=False)

    def deprovision(
        self, instance_id: str, details: DeprovisionDetails, async_allowed: bool, **kwargs
    ) -> DeprovisionServiceSpec:
        self._require_service(details)
        if self._store.get_instance(instance_id) is None:
            raise errors.ErrInstanceDoesNotExist()

        if self._store.list_bindings_for_instance(instance_id):
            raise errors.ErrInvalidParameters(
                "service instance still has bindings; unbind them before deprovisioning"
            )

        names = self._names(instance_id)
        logger.info("Deprovisioning logical database %s", names.database)
        self._admin.deprovision(names, self._settings.storage_mode)
        self._store.delete_instance(instance_id)
        return DeprovisionServiceSpec(is_async=False)

    # -- binding lifecycle -----------------------------------------------------

    def bind(
        self, instance_id: str, binding_id: str, details: BindDetails, async_allowed: bool, **kwargs
    ) -> Binding:
        self._require_service(details)
        instance = self._store.get_instance(instance_id)
        if instance is None:
            raise errors.ErrInstanceDoesNotExist()

        try:
            spec = resolve_binding_params(self._get_plan(instance["plan_id"]), details.parameters)
        except ValueError as exc:
            raise errors.ErrInvalidParameters(str(exc)) from exc

        existing = self._store.get_binding(binding_id)
        if existing is not None:
            same_instance = existing["instance_id"] == instance_id
            same_params = json.loads(existing["params_json"]) == spec.as_dict()
            if same_instance and same_params:
                return Binding(
                    state=BindState.IDENTICAL_ALREADY_EXISTS,
                    credentials=json.loads(existing["credentials_json"]),
                )
            raise errors.ErrBindingAlreadyExists()

        names = self._names(instance_id)
        username = user_for(binding_id, self._settings.name_prefix)
        instance_spec = InstanceParams(**json.loads(instance["params_json"]))
        logger.info("Binding user %s to %s as %s", username, names.database, spec.access_role)
        try:
            password = self._admin.bind(names, username, spec, instance_spec, self._settings.storage_mode)
        except AlreadyExistsError:
            raise errors.ErrBindingAlreadyExists() from None
        credentials = self._credentials(names.database, username, password)
        self._store.put_binding(
            binding_id, instance_id, username, spec.access_role, spec.as_dict(), credentials
        )
        return Binding(credentials=credentials)

    def unbind(
        self, instance_id: str, binding_id: str, details: UnbindDetails, async_allowed: bool, **kwargs
    ) -> UnbindSpec:
        self._require_service(details)
        instance = self._store.get_instance(instance_id)
        existing = self._store.get_binding(binding_id)
        if existing is None or instance is None or existing["instance_id"] != instance_id:
            raise errors.ErrBindingDoesNotExist()

        names = self._names(instance_id)
        logger.info("Unbinding user %s from %s", existing["username"], names.database)
        self._admin.unbind(names, existing["username"])
        self._store.delete_binding(binding_id)
        return UnbindSpec(is_async=False)

    # -- retrieval / polling -----------------------------------------------------

    def get_instance(self, instance_id: str, **kwargs) -> GetInstanceDetailsSpec:
        instance = self._store.get_instance(instance_id)
        if instance is None:
            raise errors.ErrInstanceDoesNotExist()
        return GetInstanceDetailsSpec(
            service_id=instance["service_id"],
            plan_id=instance["plan_id"],
            parameters=json.loads(instance["params_json"]),
        )

    def get_binding(self, instance_id: str, binding_id: str, **kwargs) -> GetBindingSpec:
        existing = self._store.get_binding(binding_id)
        if existing is None or existing["instance_id"] != instance_id:
            raise errors.ErrBindingDoesNotExist()
        return GetBindingSpec(
            credentials=json.loads(existing["credentials_json"]),
            parameters=json.loads(existing["params_json"]),
        )

    def last_operation(self, instance_id, operation_data, service_id, plan_id, **kwargs) -> LastOperation:
        # All operations are synchronous, so a poll can only find them done.
        if self._store.get_instance(instance_id) is None:
            raise errors.ErrInstanceDoesNotExist()
        return LastOperation(state=OperationState.SUCCEEDED, description="synchronous operation")

    def last_binding_operation(
        self, instance_id, binding_id, operation_data, service_id, plan_id, **kwargs
    ) -> LastOperation:
        if self._store.get_binding(binding_id) is None:
            raise errors.ErrBindingDoesNotExist()
        return LastOperation(state=OperationState.SUCCEEDED, description="synchronous operation")
