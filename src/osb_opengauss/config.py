"""Configuration: reads the environment once and hands back a Settings object.

This file is the only place that knows environment variable names.
It contains no behaviour beyond parsing and validating them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEV_BROKER_USERNAME = "broker"
# Development fallback only; app.py logs a warning when it is in use.
DEV_BROKER_PASSWORD = "broker-dev-password"  # nosec B105

STORAGE_MODES = ("role_quota", "tablespace")


@dataclass(frozen=True)
class Settings:
    # Admin connection to the openGauss/GaussDB instance.
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "gaussdb"
    db_password: str = ""
    db_admin_name: str = "postgres"
    db_sslmode: str = "disable"
    db_connect_timeout: int = 10

    # Basic-auth credentials the platform must use to talk to this broker.
    broker_username: str = DEV_BROKER_USERNAME
    broker_password: str = DEV_BROKER_PASSWORD

    # Local SQLite file used for idempotency and credential bookkeeping.
    state_db_path: str = "osb-opengauss-state.sqlite3"

    # Prefix for every database / role / user the broker creates.
    name_prefix: str = "gdb"

    # Dev HTTP server settings (production should run behind gunicorn).
    host: str = "127.0.0.1"
    port: int = 5000

    # How storage sizing is enforced:
    #   role_quota  - PERM/TEMP/SPILL SPACE quotas on the tenant roles
    #                 (needs workload management enabled on the server).
    #   tablespace  - a dedicated per-tenant tablespace with MAXSIZE, set as
    #                 the database default (hard storage cap per node);
    #                 admin user must be sysadmin / gs_roles_tablespace.
    storage_mode: str = "role_quota"

    # Operator-curated tablespaces offered to users as an enum on the
    # `tablespace` provision parameter (role_quota mode only).  Empty = the
    # parameter is not offered at all.
    tablespaces: tuple = ()

    @classmethod
    def from_env(cls) -> Settings:
        storage_mode = os.environ.get("GAUSSDB_STORAGE_MODE", cls.storage_mode)
        if storage_mode not in STORAGE_MODES:
            raise ValueError(f"GAUSSDB_STORAGE_MODE must be one of {STORAGE_MODES}, got {storage_mode!r}")
        raw_tablespaces = os.environ.get("GAUSSDB_TABLESPACES", "")
        tablespaces = tuple(t.strip() for t in raw_tablespaces.split(",") if t.strip())
        return cls(
            db_host=os.environ.get("GAUSSDB_HOST", cls.db_host),
            db_port=int(os.environ.get("GAUSSDB_PORT", cls.db_port)),
            db_user=os.environ.get("GAUSSDB_ADMIN_USER", cls.db_user),
            db_password=os.environ.get("GAUSSDB_ADMIN_PASSWORD", ""),
            db_admin_name=os.environ.get("GAUSSDB_ADMIN_DB", cls.db_admin_name),
            db_sslmode=os.environ.get("GAUSSDB_SSLMODE", cls.db_sslmode),
            db_connect_timeout=int(os.environ.get("GAUSSDB_CONNECT_TIMEOUT", cls.db_connect_timeout)),
            broker_username=os.environ.get("BROKER_USERNAME", cls.broker_username),
            broker_password=os.environ.get("BROKER_PASSWORD", cls.broker_password),
            state_db_path=os.environ.get("STATE_DB_PATH", cls.state_db_path),
            name_prefix=os.environ.get("GAUSSDB_NAME_PREFIX", cls.name_prefix),
            host=os.environ.get("BROKER_HOST", cls.host),
            port=int(os.environ.get("BROKER_PORT", cls.port)),
            storage_mode=storage_mode,
            tablespaces=tablespaces,
        )
