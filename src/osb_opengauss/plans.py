"""The offering: service, plans and the OSB catalog object.

This file answers one question only: what does the broker sell?

Each plan is a quota bundle. It does NOT know how requests are validated
(params.py), how the quotas reach openGauss (gaussdb.py) or how the OSB
endpoints are served (broker.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from openbrokerapi.catalog import (
    Schemas,
    ServiceMetadata,
    ServicePlan,
    ServicePlanMetadata,
)
from openbrokerapi.service_broker import Service

from .params import binding_schema, instance_schema

SERVICE_ID = "4c6f6a1e-0f5a-4a5b-9d7e-2f8b3a1c5e01"
SERVICE_NAME = "gaussdb"


@dataclass(frozen=True)
class PlanSpec:
    """A quota bundle; the only difference between plans are these numbers."""

    id: str
    name: str
    description: str
    storage_gb: int  # PERM SPACE / tablespace MAXSIZE
    temp_gb: int  # TEMP SPACE
    spill_gb: int  # SPILL SPACE
    max_connections: int  # database CONNECTION LIMIT


PLANS: tuple[PlanSpec, ...] = (
    PlanSpec(
        id="gaussdb-dev",
        name="dev",
        description="Small logical database for development and CI.",
        storage_gb=5,
        temp_gb=1,
        spill_gb=1,
        max_connections=20,
    ),
    PlanSpec(
        id="gaussdb-standard",
        name="standard",
        description="Standard logical database for production workloads.",
        storage_gb=50,
        temp_gb=10,
        spill_gb=10,
        max_connections=100,
    ),
    PlanSpec(
        id="gaussdb-pro",
        name="pro",
        description="Large logical database with high connection counts.",
        storage_gb=200,
        temp_gb=40,
        spill_gb=40,
        max_connections=500,
    ),
)

PLAN_INDEX: dict[str, PlanSpec] = {p.id: p for p in PLANS}


def get_plan(plan_id: str) -> PlanSpec:
    try:
        return PLAN_INDEX[plan_id]
    except KeyError:
        raise ValueError(f"Unknown plan_id {plan_id!r}") from None


def build_service(tablespaces: tuple = ()) -> Service:
    """Assemble the /v2/catalog payload for the offering."""
    plans = [
        ServicePlan(
            id=plan.id,
            name=plan.name,
            description=plan.description,
            free=True,
            metadata=ServicePlanMetadata(
                displayName=f"GaussDB {plan.name}",
                bullets=[
                    f"{plan.storage_gb} GB storage quota",
                    f"{plan.temp_gb} GB temp / {plan.spill_gb} GB spill quota",
                    f"up to {plan.max_connections} concurrent connections",
                ],
            ),
            schemas=Schemas(
                service_instance=instance_schema(plan, tablespaces),
                service_binding=binding_schema(plan),
            ),
        )
        for plan in PLANS
    ]
    return Service(
        id=SERVICE_ID,
        name=SERVICE_NAME,
        description=(
            "openGauss/GaussDB logical databases as multi-tenant service instances. "
            "Each instance is an isolated logical database; bindings are user "
            "accounts scoped to that database."
        ),
        bindable=True,
        plans=plans,
        tags=["gaussdb", "opengauss", "postgresql", "database", "sql"],
        metadata=ServiceMetadata(
            displayName="GaussDB (openGauss)",
            longDescription=(
                "Provisions logical databases (tenants) on a shared openGauss "
                "instance. Isolation: per-database connection separation, "
                "PRIVATE OBJECT filtering and per-user space quotas."
            ),
            providerDisplayName="openGauss",
            documentationUrl="https://docs.opengauss.org/",
            supportUrl="https://opengauss.org/",
        ),
        plan_updateable=False,
        instances_retrievable=True,
        bindings_retrievable=True,
    )
