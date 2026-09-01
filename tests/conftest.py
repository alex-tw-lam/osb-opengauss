import re
from types import SimpleNamespace

import openbrokerapi.settings
import pytest

# Broker-level tests construct ProvisionDetails/BindDetails directly; the
# Cloud Foundry org/space GUID requirement does not apply (Kubernetes etc.).
openbrokerapi.settings.DISABLE_SPACE_ORG_GUID_CHECK = True


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._last_sql = sql
        self._conn.executed.append(sql)

    def fetchone(self):
        """Answer the broker's existence probes against the factory's sets."""
        factory = self._conn.factory
        sql = self._last_sql
        for table, column, existing in (
            ("pg_database", "datname", factory.databases),
            ("pg_roles", "rolname", factory.roles),
            ("pg_tablespace", "spcname", factory.tablespaces),
        ):
            if f"FROM {table}" in sql:
                m = re.search(rf"{column} = '([^']+)'", sql)
                if m and m.group(1) in existing:
                    return (1,)
                return None
        return None


class FakeConnection:
    def __init__(self, dbname, factory):
        self.dbname = dbname
        self.factory = factory
        self.executed = []
        self.autocommit = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


class FakeConnect:
    """Stands in for psycopg2.connect; records every executed statement."""

    def __init__(self):
        self.connections = []
        # Names that "already exist in openGauss" for collision test frames.
        self.databases = set()
        self.roles = set()
        self.tablespaces = set()

    def __call__(self, dbname=None):
        conn = FakeConnection(dbname or "postgres", self)
        self.connections.append(conn)
        return conn

    def all_statements(self):
        return [(c.dbname, s) for c in self.connections for s in c.executed]

    def statements(self, dbname):
        return [s for conn_db, s in self.all_statements() if conn_db == dbname]

    def sql_text(self):
        return "\n".join(s for _, s in self.all_statements())


# The hermetic test plan catalog. Tests may hardcode these ids and numbers;
# they reference THIS file, never the deployment's plans.toml.
TEST_PLANS_TOML = """\
[[plan]]
id = "gaussdb-dev"
name = "dev"
description = "Small logical database for development and CI."
storage_gb = 5
temp_gb = 1
spill_gb = 1
max_connections = 20

[[plan]]
id = "gaussdb-standard"
name = "standard"
description = "Standard logical database for production workloads."
storage_gb = 50
temp_gb = 10
spill_gb = 10
max_connections = 100

[[plan]]
id = "gaussdb-pro"
name = "pro"
description = "Large logical database with high connection counts."
storage_gb = 200
temp_gb = 40
spill_gb = 40
max_connections = 500
"""


def make_env(tmp_path, **settings_overrides):
    from osb_opengauss.broker import GaussDbBroker
    from osb_opengauss.config import Settings
    from osb_opengauss.gaussdb import GaussDBAdmin
    from osb_opengauss.state import StateStore

    fake = FakeConnect()
    plans_file = tmp_path / "plans.toml"
    plans_file.write_text(TEST_PLANS_TOML)
    defaults = dict(
        db_host="db.example.org",
        db_port=6789,
        db_user="admin",
        db_password="admin-secret",
        state_db_path=str(tmp_path / "state.sqlite3"),
        plans_file=str(plans_file),
    )
    defaults.update(settings_overrides)
    settings = Settings(**defaults)
    store = StateStore(settings.state_db_path)
    admin = GaussDBAdmin(settings, connect=fake)
    broker = GaussDbBroker(admin, store, settings)
    return SimpleNamespace(fake=fake, settings=settings, store=store, broker=broker)


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


@pytest.fixture
def make_app(tmp_path):
    from osb_opengauss.app import create_app

    return create_app(make_env(tmp_path).settings)
