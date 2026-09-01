"""Validation of the plans.toml data file (the loader is the gatekeeper)."""

import pytest
from conftest import FakeConnect, make_env

from osb_opengauss.plans import load_plans

VALID = """\
[[plan]]
id = "p1"
name = "one"
description = "first"
storage_gb = 5
temp_gb = 1
spill_gb = 1
max_connections = 20

[[plan]]
id = "p2"
name = "two"
description = "second"
storage_gb = 50
temp_gb = 10
spill_gb = 10
max_connections = 100
free = false
"""


def write(tmp_path, content):
    path = tmp_path / "plans.toml"
    path.write_text(content)
    return str(path)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="plans file not found"):
        load_plans(str(tmp_path / "nope.toml"))


def test_file_without_plans_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="no \\[\\[plan\\]\\] entries"):
        load_plans(write(tmp_path, "# nothing here\n"))


def test_missing_field_is_rejected(tmp_path):
    body = VALID.replace("temp_gb = 1\n", "")
    with pytest.raises(ValueError, match="missing fields.*temp_gb"):
        load_plans(write(tmp_path, body))


def test_non_positive_quota_is_rejected(tmp_path):
    body = VALID.replace("storage_gb = 5\n", "storage_gb = 0\n", 1)
    with pytest.raises(ValueError, match="storage_gb must be a positive integer"):
        load_plans(write(tmp_path, body))


def test_duplicate_plan_id_is_rejected(tmp_path):
    body = VALID.replace('id = "p2"', 'id = "p1"')
    with pytest.raises(ValueError, match="duplicate plan id"):
        load_plans(write(tmp_path, body))


def test_valid_file_loads(tmp_path):
    plans = load_plans(write(tmp_path, VALID))
    assert [p.id for p in plans] == ["p1", "p2"]
    assert plans[0].storage_gb == 5
    assert plans[0].free is True  # defaults to free when omitted
    assert plans[1].free is False  # explicit override


def test_broker_refuses_to_start_on_bad_plans_file(tmp_path):
    from osb_opengauss.broker import GaussDbBroker
    from osb_opengauss.config import Settings
    from osb_opengauss.gaussdb import GaussDBAdmin
    from osb_opengauss.state import StateStore

    settings = Settings(state_db_path=str(tmp_path / "state.sqlite3"), plans_file=str(tmp_path / "nope.toml"))
    store = StateStore(settings.state_db_path)
    admin = GaussDBAdmin(settings, connect=FakeConnect())
    with pytest.raises(ValueError, match="plans file not found"):
        GaussDbBroker(admin, store, settings)


def test_broker_serves_plans_from_its_own_file(tmp_path):
    # A deployment-specific plans file changes the catalog without code edits.
    custom_plans = """\
[[plan]]
id = "tenant-xl"
name = "xl"
description = "Extra large tenant."
storage_gb = 1000
temp_gb = 100
spill_gb = 100
max_connections = 2000
"""
    custom = tmp_path / "custom-plans.toml"
    custom.write_text(custom_plans)
    env = make_env(tmp_path, plans_file=str(custom))
    service = env.broker.catalog()
    assert [p.id for p in service.plans] == ["tenant-xl"]
    assert service.plans[0].metadata.bullets[0] == "1000 GB storage quota"
