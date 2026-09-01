import pytest
from openbrokerapi import errors
from openbrokerapi.service_broker import (
    BindDetails,
    BindState,
    DeprovisionDetails,
    UnbindDetails,
)

from osb_opengauss.gaussdb import names_for, user_for
from osb_opengauss.plans import SERVICE_ID

IID = "11111111-1111-1111-1111-111111111111"
BID = "22222222-2222-2222-2222-222222222222"
DB = names_for(IID, "gdb").database
USER = user_for(BID, "gdb")


def bind_details(params=None):
    return BindDetails(service_id=SERVICE_ID, plan_id="gaussdb-dev", parameters=params)


def unbind_details():
    return UnbindDetails(service_id=SERVICE_ID, plan_id="gaussdb-dev")


def provision(env):
    from openbrokerapi.service_broker import ProvisionDetails

    env.broker.provision(
        IID,
        ProvisionDetails(
            service_id=SERVICE_ID,
            plan_id="gaussdb-dev",
            organization_guid="org",
            space_guid="space",
        ),
        async_allowed=False,
    )


def test_bind_creates_scoped_user(env):
    provision(env)
    binding = env.broker.bind(IID, BID, bind_details(), async_allowed=False)

    admin = env.fake.statements("postgres")
    assert any(
        s.startswith(f'CREATE USER "{USER}" LOGIN PASSWORD ') and s.endswith("CONNECTION LIMIT 20")
        for s in admin
    )
    assert f'GRANT "{DB}_rw" TO "{USER}"' in admin
    assert f"ALTER ROLE \"{USER}\" PERM SPACE '5G'" in admin
    assert f"ALTER ROLE \"{USER}\" SET search_path = '{DB}_data, public'" in admin

    creds = binding.credentials
    assert creds["database"] == DB
    assert creds["username"] == USER
    assert len(creds["password"]) == 28
    assert creds["uri"].startswith(f"postgresql://{USER}:")
    assert "@db.example.org:6789/" + DB in creds["uri"]


def test_bind_access_roles(env):
    provision(env)
    env.broker.bind(IID, BID, bind_details({"access_role": "readonly"}), async_allowed=False)
    assert f'GRANT "{DB}_ro" TO "{USER}"' in env.fake.statements("postgres")

    other = "33333333-3333-3333-3333-333333333333"
    env.broker.bind(IID, other, bind_details({"access_role": "owner"}), async_allowed=False)
    assert f'GRANT "{DB}_own" TO "{user_for(other, "gdb")}"' in env.fake.statements("postgres")


def test_bind_validates_params(env):
    provision(env)
    with pytest.raises(errors.ErrInvalidParameters):
        env.broker.bind(IID, BID, bind_details({"access_role": "superuser"}), async_allowed=False)


def test_bind_requires_existing_instance(env):
    with pytest.raises(errors.ErrInstanceDoesNotExist):
        env.broker.bind(IID, BID, bind_details(), async_allowed=False)


def test_bind_is_idempotent(env):
    provision(env)
    first = env.broker.bind(IID, BID, bind_details(), async_allowed=False)
    second = env.broker.bind(IID, BID, bind_details(), async_allowed=False)
    assert second.state == BindState.IDENTICAL_ALREADY_EXISTS
    assert second.credentials == first.credentials
    assert sum(1 for _, s in env.fake.all_statements() if s.startswith(f'CREATE USER "{USER}"')) == 1

    with pytest.raises(errors.ErrBindingAlreadyExists):
        env.broker.bind(IID, BID, bind_details({"access_role": "readonly"}), async_allowed=False)


def test_unbind_drops_user(env):
    provision(env)
    env.broker.bind(IID, BID, bind_details(), async_allowed=False)
    env.broker.unbind(IID, BID, unbind_details(), async_allowed=False)

    assert f'DROP OWNED BY "{USER}" CASCADE' in env.fake.statements(DB)
    assert f'DROP USER IF EXISTS "{USER}"' in env.fake.statements("postgres")
    assert env.store.get_binding(BID) is None

    with pytest.raises(errors.ErrBindingDoesNotExist):
        env.broker.unbind(IID, BID, unbind_details(), async_allowed=False)


def test_get_binding_round_trip(env):
    provision(env)
    binding = env.broker.bind(IID, BID, bind_details({"access_role": "readonly"}), async_allowed=False)
    fetched = env.broker.get_binding(IID, BID)
    assert fetched.credentials == binding.credentials
    assert fetched.parameters["access_role"] == "readonly"


def test_deprovision_requires_no_bindings(env):
    provision(env)
    env.broker.bind(IID, BID, bind_details(), async_allowed=False)

    with pytest.raises(errors.ErrInvalidParameters):
        env.broker.deprovision(
            IID, DeprovisionDetails(service_id=SERVICE_ID, plan_id="gaussdb-dev"), async_allowed=False
        )


def test_deprovision_after_unbind(env):
    provision(env)
    env.broker.bind(IID, BID, bind_details(), async_allowed=False)
    env.broker.unbind(IID, BID, unbind_details(), async_allowed=False)
    env.broker.deprovision(
        IID, DeprovisionDetails(service_id=SERVICE_ID, plan_id="gaussdb-dev"), async_allowed=False
    )

    admin = env.fake.statements("postgres")
    assert f'DROP DATABASE IF EXISTS "{DB}"' in admin
    for role in ("_rw", "_ro", "_own"):
        assert f'DROP ROLE IF EXISTS "{DB}{role}"' in admin
    assert env.store.get_instance(IID) is None

    with pytest.raises(errors.ErrInstanceDoesNotExist):
        env.broker.deprovision(
            IID, DeprovisionDetails(service_id=SERVICE_ID, plan_id="gaussdb-dev"), async_allowed=False
        )
