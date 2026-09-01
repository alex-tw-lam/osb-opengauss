import pytest
from openbrokerapi import errors
from openbrokerapi.service_broker import ProvisionDetails, ProvisionState

from osb_opengauss.gaussdb import names_for
from osb_opengauss.plans import SERVICE_ID

IID = "11111111-1111-1111-1111-111111111111"
DB = names_for(IID, "gdb").database


def details(plan="gaussdb-dev", params=None):
    return ProvisionDetails(
        service_id=SERVICE_ID,
        plan_id=plan,
        parameters=params,
        organization_guid="org",
        space_guid="space",
    )


def test_provision_emits_expected_sql(env):
    env.broker.provision(IID, details(), async_allowed=False)

    admin = env.fake.statements("postgres")
    assert (
        f'CREATE DATABASE "{DB}" OWNER "{DB}_own" TEMPLATE template0'
        " ENCODING 'UTF8' DBCOMPATIBILITY 'PG' CONNECTION LIMIT 20" in admin
    )
    assert f'REVOKE CONNECT ON DATABASE "{DB}" FROM PUBLIC' in admin
    assert f'GRANT CONNECT ON DATABASE "{DB}" TO "{DB}_rw"' in admin
    assert f'GRANT CONNECT ON DATABASE "{DB}" TO "{DB}_ro"' in admin
    assert f"ALTER ROLE \"{DB}_own\" PERM SPACE '5G'" in admin
    assert f"ALTER ROLE \"{DB}_own\" TEMP SPACE '1G'" in admin
    assert f"ALTER ROLE \"{DB}_own\" SPILL SPACE '1G'" in admin

    tenant = env.fake.statements(DB)
    assert f'ALTER DATABASE "{DB}" ENABLE PRIVATE OBJECT' in tenant
    assert f'CREATE SCHEMA "{DB}_data" AUTHORIZATION "{DB}_own"' in tenant
    assert f'GRANT USAGE, CREATE ON SCHEMA "{DB}_data" TO "{DB}_rw"' in tenant
    assert f'GRANT USAGE ON SCHEMA "{DB}_data" TO "{DB}_ro"' in tenant
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in tenant
    assert any("ALTER DEFAULT PRIVILEGES" in s and "GRANT SELECT ON TABLES" in s for s in tenant)

    assert env.store.get_instance(IID)["db_name"] == DB


def test_provision_honours_parameters(env):
    env.broker.provision(
        IID,
        details(
            params={
                "compatibility": "A",
                "encoding": "GBK",
                "max_connections": 10,
                "storage_gb": 2,
            },
        ),
        async_allowed=False,
    )
    admin = env.fake.statements("postgres")
    assert any(
        "CREATE DATABASE" in s
        and "DBCOMPATIBILITY 'A'" in s
        and "ENCODING 'GBK'" in s
        and "CONNECTION LIMIT 10" in s
        for s in admin
    )
    assert f"ALTER ROLE \"{DB}_own\" PERM SPACE '2G'" in admin


def test_provision_rejects_quota_above_plan(env):
    with pytest.raises(errors.ErrInvalidParameters):
        env.broker.provision(IID, details(params={"storage_gb": 999}), async_allowed=False)
    with pytest.raises(errors.ErrInvalidParameters):
        env.broker.provision(IID, details(params={"compatibility": "ORACLE"}), async_allowed=False)


def test_provision_unknown_plan(env):
    with pytest.raises(errors.ErrInvalidParameters):
        env.broker.provision(IID, details(plan="gaussdb-mega"), async_allowed=False)


def test_provision_is_idempotent(env):
    first = env.broker.provision(IID, details(), async_allowed=False)
    second = env.broker.provision(IID, details(), async_allowed=False)
    assert first.state == ProvisionState.SUCCESSFUL_CREATED
    assert second.state == ProvisionState.IDENTICAL_ALREADY_EXISTS
    assert sum(1 for _, s in env.fake.all_statements() if "CREATE DATABASE" in s) == 1


def test_provision_conflicting_change_is_409(env):
    env.broker.provision(IID, details(), async_allowed=False)
    with pytest.raises(errors.ErrInstanceAlreadyExists):
        env.broker.provision(IID, details(params={"max_connections": 5}), async_allowed=False)


def test_update_adjusts_limits(env):
    env.broker.provision(IID, details(), async_allowed=False)
    from openbrokerapi.service_broker import UpdateDetails

    env.broker.update(
        IID,
        UpdateDetails(
            service_id=SERVICE_ID,
            plan_id="gaussdb-dev",
            parameters={"max_connections": 10, "storage_gb": 3},
            previous_values={"plan_id": "gaussdb-dev"},
        ),
        async_allowed=False,
    )
    admin = env.fake.statements("postgres")
    assert f'ALTER DATABASE "{DB}" CONNECTION LIMIT = 10' in admin
    assert f"ALTER ROLE \"{DB}_own\" PERM SPACE '3G'" in admin
    assert env.broker.get_instance(IID).parameters["storage_gb"] == 3


def test_update_rejects_plan_change(env):
    from openbrokerapi.service_broker import UpdateDetails

    env.broker.provision(IID, details(), async_allowed=False)
    with pytest.raises(errors.ErrPlanChangeNotSupported):
        env.broker.update(
            IID,
            UpdateDetails(
                service_id=SERVICE_ID,
                plan_id="gaussdb-pro",
                previous_values={"plan_id": "gaussdb-dev"},
            ),
            async_allowed=False,
        )
