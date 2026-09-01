"""Category Partition Method (CPM) test frames.

Aspects and the partitions chosen for each:

  A1 operation              : provision | update | bind | unbind | deprovision
  A2 instance record        : absent | present
  A3 binding record         : absent | present
  A4 request parameters     : default | valid-custom | conflicting |
                              invalid-enum | out-of-range | unknown-plan |
                              ts-choice | bad-access-role | plan-change
  A5 physical divergence    : clean | db-name-collision | owner-role-collision |
                              user-name-collision   (object exists in openGauss
                              without the broker knowing about it)
  A6 storage mode           : role_quota | tablespace
  A7 bindings at deprovision: none | some

Every row in FRAMES is one combination (test frame); the parametrized test
instantiates each frame against a fresh broker + fake openGauss and checks the
expected result (OSB result object/state or mapped OSB error).

The same table is rendered in docs/cpm-tests.md.
"""

import json

import pytest
from conftest import make_env
from openbrokerapi import errors
from openbrokerapi.service_broker import (
    BindDetails,
    BindState,
    DeprovisionDetails,
    ProvisionDetails,
    ProvisionState,
    UnbindDetails,
    UpdateDetails,
)

from osb_opengauss.gaussdb import names_for, user_for
from osb_opengauss.plans import SERVICE_ID

IID = "11111111-1111-1111-1111-111111111111"
BID = "22222222-2222-2222-2222-222222222222"
DB = names_for(IID, "gdb").database
USER = user_for(BID, "gdb")


# -- request builders ---------------------------------------------------------

PROV_PARAMS = {
    "default": None,
    "conflicting": {"max_connections": 5},
    "invalid-enum": {"compatibility": "ORCL"},
    "out-of-range": {"storage_gb": 999},
    "ts-choice": {"tablespace": "ts_ssd"},
}
UPDATE_PARAMS = {
    "custom": {"max_connections": 10},
    "storage": {"storage_gb": 3},
}
BIND_PARAMS = {
    "default": None,
    "conflicting": {"access_role": "readonly"},
    "bad-role": {"access_role": "root"},
}


def prov_details(params, plan="gaussdb-dev"):
    return ProvisionDetails(
        service_id=SERVICE_ID,
        plan_id=plan,
        parameters=params,
        organization_guid="org",
        space_guid="space",
    )


def update_details(params, plan="gaussdb-dev", previous="gaussdb-dev"):
    return UpdateDetails(
        service_id=SERVICE_ID,
        plan_id=plan,
        parameters=params,
        previous_values={"plan_id": previous},
    )


def bind_details(params):
    return BindDetails(service_id=SERVICE_ID, plan_id="gaussdb-dev", parameters=params)


def unbind_details():
    return UnbindDetails(service_id=SERVICE_ID, plan_id="gaussdb-dev")


def deprovision_details():
    return DeprovisionDetails(service_id=SERVICE_ID, plan_id="gaussdb-dev")


# -- the frames ---------------------------------------------------------------

FRAMES = [
    # F01 provision / clean / role_quota
    dict(
        id="F01",
        aspects="A1=provision A2=absent A4=default A5=clean A6=role_quota",
        arrange=[],
        act=("provision", "default"),
        expect=("created",),
        sql=["CREATE DATABASE", "PERM SPACE '5G'", "ENABLE PRIVATE OBJECT"],
    ),
    # F02 identical re-provision
    dict(
        id="F02",
        aspects="A1=provision A2=present A4=default",
        arrange=["provision"],
        act=("provision", "default"),
        expect=("identical",),
    ),
    # F03 conflicting re-provision
    dict(
        id="F03",
        aspects="A1=provision A2=present A4=conflicting",
        arrange=["provision"],
        act=("provision", "conflicting"),
        expect=("error", errors.ErrInstanceAlreadyExists),
    ),
    # F04 invalid enum value
    dict(
        id="F04",
        aspects="A1=provision A2=absent A4=invalid-enum",
        arrange=[],
        act=("provision", "invalid-enum"),
        expect=("error", errors.ErrInvalidParameters),
        no_sql=["CREATE DATABASE"],
    ),
    # F05 out-of-range value
    dict(
        id="F05",
        aspects="A1=provision A2=absent A4=out-of-range",
        arrange=[],
        act=("provision", "out-of-range"),
        expect=("error", errors.ErrInvalidParameters),
        no_sql=["CREATE DATABASE"],
    ),
    # F06 unknown plan
    dict(
        id="F06",
        aspects="A1=provision A2=absent A4=unknown-plan",
        arrange=[],
        act=("provision-plan", "gaussdb-mega"),
        expect=("error", errors.ErrInvalidParameters),
        no_sql=["CREATE DATABASE"],
    ),
    # F07 database name already exists in openGauss (broker state lost)
    dict(
        id="F07",
        aspects="A1=provision A2=absent A5=db-collision",
        arrange=["db-collision"],
        act=("provision", "default"),
        expect=("error", errors.ErrInstanceAlreadyExists),
        no_sql=["CREATE DATABASE"],
    ),
    # F08 owner role name already exists in openGauss
    dict(
        id="F08",
        aspects="A1=provision A2=absent A5=own-role-collision",
        arrange=["own-role-collision"],
        act=("provision", "default"),
        expect=("error", errors.ErrInstanceAlreadyExists),
        no_sql=["CREATE DATABASE"],
    ),
    # F09 provision in tablespace storage mode
    dict(
        id="F09",
        aspects="A1=provision A6=tablespace",
        mode="tablespace",
        arrange=[],
        act=("provision", "default"),
        expect=("created",),
        sql=["CREATE TABLESPACE", "MAXSIZE '5GB'", "TABLESPACE"],
        no_sql=["PERM SPACE"],
    ),
    # F10 user picks a tablespace while the broker runs tablespace mode
    dict(
        id="F10",
        aspects="A1=provision A4=ts-choice A6=tablespace",
        mode="tablespace",
        tablespaces=("ts_ssd", "ts_hdd"),
        arrange=[],
        act=("provision", "ts-choice"),
        expect=("error", errors.ErrInvalidParameters),
    ),
    # F11 user picks from the operator-curated tablespace enum (role_quota mode)
    dict(
        id="F11",
        aspects="A1=provision A4=ts-choice A6=role_quota",
        tablespaces=("ts_ssd", "ts_hdd"),
        arrange=[],
        act=("provision", "ts-choice"),
        expect=("created",),
        sql=['TABLESPACE "ts_ssd"'],
    ),
    # F12 update instance parameters
    dict(
        id="F12",
        aspects="A1=update A2=present A4=valid-custom",
        arrange=["provision"],
        act=("update", "custom"),
        expect=("updated",),
        sql=["CONNECTION LIMIT = 10"],
    ),
    # F13 update unknown instance
    dict(
        id="F13",
        aspects="A1=update A2=absent",
        arrange=[],
        act=("update", "custom"),
        expect=("error", errors.ErrInstanceDoesNotExist),
    ),
    # F14 plan change rejected
    dict(
        id="F14",
        aspects="A1=update A2=present A4=plan-change",
        arrange=["provision"],
        act=("update-plan", "gaussdb-pro"),
        expect=("error", errors.ErrPlanChangeNotSupported),
    ),
    # F15 storage resize in tablespace mode goes through RESIZE MAXSIZE
    dict(
        id="F15",
        aspects="A1=update A2=present A4=storage A6=tablespace",
        mode="tablespace",
        arrange=["provision"],
        act=("update", "storage"),
        expect=("updated",),
        sql=["RESIZE MAXSIZE '3GB'"],
    ),
    # F16 bind, happy path
    dict(
        id="F16",
        aspects="A1=bind A2=present A3=absent A4=default A5=clean",
        arrange=["provision"],
        act=("bind", "default"),
        expect=("bound",),
        sql=["CREATE USER", "PERM SPACE '5G'"],
    ),
    # F17 identical re-bind returns the same credentials
    dict(
        id="F17",
        aspects="A1=bind A3=present A4=default",
        arrange=["provision", "bind"],
        act=("bind", "default"),
        expect=("bound-identical",),
    ),
    # F18 conflicting re-bind
    dict(
        id="F18",
        aspects="A1=bind A3=present A4=conflicting",
        arrange=["provision", "bind"],
        act=("bind", "conflicting"),
        expect=("error", errors.ErrBindingAlreadyExists),
    ),
    # F19 bind against unknown instance
    dict(
        id="F19",
        aspects="A1=bind A2=absent",
        arrange=[],
        act=("bind", "default"),
        expect=("error", errors.ErrInstanceDoesNotExist),
        no_sql=["CREATE USER"],
    ),
    # F20 username already exists in openGauss (state lost)
    dict(
        id="F20",
        aspects="A1=bind A3=absent A5=user-collision",
        arrange=["provision", "user-collision"],
        act=("bind", "default"),
        expect=("error", errors.ErrBindingAlreadyExists),
        no_sql=["CREATE USER"],
    ),
    # F21 invalid access-role enum
    dict(
        id="F21",
        aspects="A1=bind A3=absent A4=bad-access-role",
        arrange=["provision"],
        act=("bind", "bad-role"),
        expect=("error", errors.ErrInvalidParameters),
        no_sql=["CREATE USER"],
    ),
    # F22 unbind happy path
    dict(
        id="F22",
        aspects="A1=unbind A3=present",
        arrange=["provision", "bind"],
        act=("unbind",),
        expect=("unbound",),
        sql=["DROP OWNED BY", "DROP USER IF EXISTS"],
        state_absent_binding=True,
    ),
    # F23 unbind unknown binding
    dict(
        id="F23",
        aspects="A1=unbind A3=absent",
        arrange=["provision"],
        act=("unbind",),
        expect=("error", errors.ErrBindingDoesNotExist),
    ),
    # F24 deprovision blocked while bindings exist
    dict(
        id="F24",
        aspects="A1=deprovision A2=present A7=some",
        arrange=["provision", "bind"],
        act=("deprovision",),
        expect=("error", errors.ErrInvalidParameters),
        no_sql=["DROP DATABASE"],
        state_present=True,
    ),
    # F25 deprovision after unbinding
    dict(
        id="F25",
        aspects="A1=deprovision A2=present A7=none",
        arrange=["provision", "bind", "unbind"],
        act=("deprovision",),
        expect=("deprovisioned",),
        sql=["DROP DATABASE IF EXISTS", "DROP ROLE IF EXISTS"],
        state_absent=True,
    ),
    # F26 deprovision unknown instance
    dict(
        id="F26",
        aspects="A1=deprovision A2=absent",
        arrange=[],
        act=("deprovision",),
        expect=("error", errors.ErrInstanceDoesNotExist),
    ),
    # F27 deprovision in tablespace mode drops the tablespace too
    dict(
        id="F27",
        aspects="A1=deprovision A2=present A7=none A6=tablespace",
        mode="tablespace",
        arrange=["provision"],
        act=("deprovision",),
        expect=("deprovisioned",),
        sql=["DROP TABLESPACE IF EXISTS"],
        state_absent=True,
    ),
]


# -- runner --------------------------------------------------------------------


def _arrange(env, ops):
    for op in ops:
        if op == "provision":
            env.broker.provision(IID, prov_details(None), async_allowed=False)
        elif op == "bind":
            env.broker.bind(IID, BID, bind_details(None), async_allowed=False)
        elif op == "unbind":
            env.broker.unbind(IID, BID, unbind_details(), async_allowed=False)
        elif op == "db-collision":
            env.fake.databases.add(DB)
        elif op == "own-role-collision":
            env.fake.roles.add(f"{DB}_own")
        elif op == "user-collision":
            env.fake.roles.add(USER)
        else:
            raise AssertionError(f"unknown arrange op {op!r}")


def _act(env, act):
    op = act[0]
    kind = act[1] if len(act) > 1 else None
    try:
        if op == "provision":
            return ("ok", env.broker.provision(IID, prov_details(PROV_PARAMS[kind]), async_allowed=False))
        if op == "provision-plan":
            return ("ok", env.broker.provision(IID, prov_details(None, plan=kind), async_allowed=False))
        if op == "update":
            return ("ok", env.broker.update(IID, update_details(UPDATE_PARAMS[kind]), async_allowed=False))
        if op == "update-plan":
            return (
                "ok",
                env.broker.update(
                    IID, update_details(None, plan=kind, previous="gaussdb-dev"), async_allowed=False
                ),
            )
        if op == "bind":
            return ("ok", env.broker.bind(IID, BID, bind_details(BIND_PARAMS[kind]), async_allowed=False))
        if op == "unbind":
            return ("ok", env.broker.unbind(IID, BID, unbind_details(), async_allowed=False))
        if op == "deprovision":
            return ("ok", env.broker.deprovision(IID, deprovision_details(), async_allowed=False))
        raise AssertionError(f"unknown act op {op!r}")
    except Exception as exc:  # noqa: BLE001 - recorded and matched below
        return ("error", type(exc))


def _verify(env, frame, outcome):
    kind = frame["expect"][0]

    if kind == "error":
        expected_exc = frame["expect"][1]
        assert outcome[0] == "error" and issubclass(outcome[1], expected_exc), (
            f"{frame['id']}: expected {expected_exc.__name__}, got {outcome!r}"
        )
    else:
        assert outcome[0] == "ok", f"{frame['id']}: unexpected error {outcome[1]!r}"
        result = outcome[1]
        if kind == "created":
            assert result.state == ProvisionState.SUCCESSFUL_CREATED
        elif kind == "identical":
            assert result.state == ProvisionState.IDENTICAL_ALREADY_EXISTS
        elif kind == "bound":
            assert result.credentials["username"] == USER
            assert result.credentials["database"] == DB
        elif kind == "bound-identical":
            assert result.state == BindState.IDENTICAL_ALREADY_EXISTS
            stored = json.loads(env.store.get_binding(BID)["credentials_json"])
            assert result.credentials == stored

    sql = env.fake.sql_text()
    for needle in frame.get("sql", []):
        assert needle in sql, f"{frame['id']}: missing SQL {needle!r}"
    for needle in frame.get("no_sql", []):
        assert needle not in sql, f"{frame['id']}: unexpected SQL {needle!r}"

    if frame.get("state_absent"):
        assert env.store.get_instance(IID) is None
    if frame.get("state_present"):
        assert env.store.get_instance(IID) is not None
    if frame.get("state_absent_binding"):
        assert env.store.get_binding(BID) is None


@pytest.mark.parametrize("frame", FRAMES, ids=[f["id"] for f in FRAMES])
def test_frame(frame, tmp_path):
    env = make_env(
        tmp_path,
        storage_mode=frame.get("mode", "role_quota"),
        tablespaces=frame.get("tablespaces", ()),
    )
    _arrange(env, frame.get("arrange", []))
    outcome = _act(env, frame["act"])
    _verify(env, frame, outcome)
