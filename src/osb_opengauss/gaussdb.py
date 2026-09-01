"""All openGauss DDL: the SQL side of provision/bind/unbind/deprovision/update.

This file is the only place that knows SQL.  It does NOT know the OSB API,
error-to-HTTP mapping, or where instance/binding ids come from; it receives
ready-made names and resolved parameters and executes plain statements.
Object names are always identifier-quoted, literals always quote-escaped.
"""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from .config import Settings
from .params import BindingParams, InstanceParams

# openGauss identifiers are capped at 63 bytes.
_MAX_ID = 63


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sanitize(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]", "", raw.lower())
    return cleaned or "x"


def quota_str(gb: int) -> str:
    """PERM/TEMP/SPILL SPACE size format, e.g. 5 -> '5G'."""
    return f"{gb}G"


@dataclass(frozen=True)
class InstanceNames:
    """Every openGauss object that belongs to one service instance."""

    database: str
    owner_role: str
    rw_role: str
    ro_role: str
    schema: str
    tablespace: str


def names_for(instance_id: str, prefix: str) -> InstanceNames:
    tail = _sanitize(instance_id)[: _MAX_ID - len(prefix) - 4]
    database = f"{prefix}_{tail}"
    return InstanceNames(
        database=database,
        owner_role=f"{database}_own",
        rw_role=f"{database}_rw",
        ro_role=f"{database}_ro",
        schema=f"{database}_data",
        tablespace=f"{database}_ts",
    )


def user_for(binding_id: str, prefix: str) -> str:
    return f"{prefix}u_{_sanitize(binding_id)[: _MAX_ID - len(prefix) - 2]}"


_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!#%*+-=?@^_~"


def generate_password(length: int = 28) -> str:
    """Random password meeting the openGauss complexity policy (3 of 4 classes).

    Re-rolls until all four character classes are present.
    """
    while True:
        password = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if (
            any(c.isupper() for c in password)
            and any(c.islower() for c in password)
            and any(c.isdigit() for c in password)
            and any(not c.isalnum() for c in password)
        ):
            return password


class AlreadyExistsError(Exception):
    """The object name we are about to create is already taken in openGauss."""


class GaussDBAdmin:
    """Executes the DDL behind provision / bind / unbind / deprovision / update."""

    def __init__(self, settings: Settings, connect: Callable | None = None):
        self._settings = settings
        self._connect = connect or self._psycopg_connect

    # -- connections ---------------------------------------------------------

    def _psycopg_connect(self, dbname: str | None = None):
        import psycopg2

        s = self._settings
        return psycopg2.connect(
            host=s.db_host,
            port=s.db_port,
            user=s.db_user,
            password=s.db_password,
            dbname=dbname or s.db_admin_name,
            sslmode=s.db_sslmode,
            connect_timeout=s.db_connect_timeout,
        )

    @contextmanager
    def _admin_conn(self):
        conn = self._connect()
        try:
            conn.autocommit = True
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tenant_conn(self, database: str):
        conn = self._connect(database)
        try:
            conn.autocommit = True
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _run(conn, statements):
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)

    def _exists(self, table: str, column: str, name: str) -> bool:
        """Probe a system catalog (pg_database / pg_roles / pg_tablespace)."""
        # table/column are hardcoded internal constants; name is escaped.
        probe = f"SELECT 1 FROM {table} WHERE {column} = {quote_literal(name)}"  # nosec B608
        with self._admin_conn() as conn, conn.cursor() as cur:
            cur.execute(probe)  # nosemgrep
            return cur.fetchone() is not None

    # -- lifecycle -----------------------------------------------------------

    def provision(self, names: InstanceNames, spec: InstanceParams, storage_mode: str = "role_quota") -> None:
        # Refuse to adopt objects that already exist in openGauss (for example
        # after the broker lost its state): report a clean conflict instead of
        # failing on raw DDL.
        if self._exists("pg_database", "datname", names.database):
            raise AlreadyExistsError(f"database {names.database} already exists")
        for role in (names.owner_role, names.rw_role, names.ro_role):
            if self._exists("pg_roles", "rolname", role):
                raise AlreadyExistsError(f"role {role} already exists")

        db = quote_ident(names.database)
        own = quote_ident(names.owner_role)
        rw = quote_ident(names.rw_role)
        ro = quote_ident(names.ro_role)

        admin_stmts = []
        if storage_mode == "tablespace":
            if self._exists("pg_tablespace", "spcname", names.tablespace):
                raise AlreadyExistsError(f"tablespace {names.tablespace} already exists")
            # A dedicated tablespace hard-caps the tenant's storage per node.
            admin_stmts.append(
                f"CREATE TABLESPACE {quote_ident(names.tablespace)} OWNER {own}"
                f" RELATIVE LOCATION {quote_literal('broker/' + names.tablespace)}"
                f" MAXSIZE {quote_literal(f'{spec.storage_gb}GB')}"
            )
            default_tablespace = quote_ident(names.tablespace)
        elif spec.tablespace:
            default_tablespace = quote_ident(spec.tablespace)
        else:
            default_tablespace = ""
        tablespace_clause = f" TABLESPACE {default_tablespace}" if default_tablespace else ""

        admin_stmts += [
            # Group roles that carry the tenant's privilege boundaries.
            f"CREATE ROLE {own} NOLOGIN",
            f"CREATE ROLE {rw} NOLOGIN",
            f"CREATE ROLE {ro} NOLOGIN",
            # The logical database itself, cloned from template0.
            f"CREATE DATABASE {db} OWNER {own} TEMPLATE template0"
            f" ENCODING {quote_literal(spec.encoding)}"
            f" DBCOMPATIBILITY {quote_literal(spec.compatibility)}"
            f"{tablespace_clause}"
            f" CONNECTION LIMIT {spec.max_connections}",
            # Connection isolation: only tenant roles may connect.
            f"REVOKE CONNECT ON DATABASE {db} FROM PUBLIC",
            f"GRANT CONNECT ON DATABASE {db} TO {rw}",
            f"GRANT CONNECT ON DATABASE {db} TO {ro}",
            # In role_quota mode PERM SPACE caps permanent storage; in
            # tablespace mode MAXSIZE already does, so only temp/spill remain.
            *self._role_quota_stmts(own, spec, include_perm=storage_mode == "role_quota"),
        ]
        with self._admin_conn() as conn:
            self._run(conn, admin_stmts)

        schema = quote_ident(names.schema)
        tenant_stmts = [
            # Object isolation: ordinary users only see objects they may access.
            f"ALTER DATABASE {db} ENABLE PRIVATE OBJECT",
            f"CREATE SCHEMA {schema} AUTHORIZATION {own}",
            f"GRANT USAGE, CREATE ON SCHEMA {schema} TO {rw}",
            f"GRANT USAGE ON SCHEMA {schema} TO {ro}",
            # Future objects created by the owner are readable per access role.
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {own} IN SCHEMA {schema} GRANT SELECT ON TABLES TO {ro}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {own} IN SCHEMA {schema} "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {rw}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {own} IN SCHEMA {schema} "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {ro}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {own} IN SCHEMA {schema} "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {rw}",
            # Lock down the default public schema of this tenant database.
            "REVOKE ALL ON SCHEMA public FROM PUBLIC",
        ]
        with self._tenant_conn(names.database) as conn:
            self._run(conn, tenant_stmts)

    @staticmethod
    def _role_quota_stmts(role_ident: str, spec: InstanceParams, include_perm: bool) -> list:
        stmts = []
        if include_perm:
            stmts.append(f"ALTER ROLE {role_ident} PERM SPACE {quote_literal(quota_str(spec.storage_gb))}")
        stmts += [
            f"ALTER ROLE {role_ident} TEMP SPACE {quote_literal(quota_str(spec.temp_gb))}",
            f"ALTER ROLE {role_ident} SPILL SPACE {quote_literal(quota_str(spec.spill_gb))}",
        ]
        return stmts

    def bind(
        self,
        names: InstanceNames,
        username: str,
        spec: BindingParams,
        instance_spec: InstanceParams,
        storage_mode: str = "role_quota",
    ) -> str:
        if self._exists("pg_roles", "rolname", username):
            raise AlreadyExistsError(f"user {username} already exists")

        password = generate_password()
        role_map = {
            "owner": names.owner_role,
            "readwrite": names.rw_role,
            "readonly": names.ro_role,
        }
        group = quote_ident(role_map[spec.access_role])
        user = quote_ident(username)
        statements = [
            f"CREATE USER {user} LOGIN PASSWORD {quote_literal(password)}"
            f" CONNECTION LIMIT {spec.max_connections}",
            f"GRANT {group} TO {user}",
            # The same space quotas on the login user, so objects created
            # directly by the user cannot bypass the tenant quota.
            *self._role_quota_stmts(user, instance_spec, include_perm=storage_mode == "role_quota"),
            f"ALTER ROLE {user} SET search_path = {quote_literal(names.schema + ', public')}",
        ]
        with self._admin_conn() as conn:
            self._run(conn, statements)
        return password

    def unbind(self, names: InstanceNames, username: str) -> None:
        user = quote_ident(username)
        with self._tenant_conn(names.database) as conn:
            self._run(
                conn,
                [
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    f" WHERE usename = {quote_literal(username)}",  # nosec B608
                    # Drop everything the binding user owns inside the tenant.
                    f"DROP OWNED BY {user} CASCADE",
                ],
            )
        with self._admin_conn() as conn:
            self._run(
                conn,
                [
                    # openGauss auto-creates a same-named schema for new users
                    # in the database they are created in; clean it up.
                    f"DROP SCHEMA IF EXISTS {user} CASCADE",
                    f"DROP USER IF EXISTS {user}",
                ],
            )

    def deprovision(self, names: InstanceNames, storage_mode: str = "role_quota") -> None:
        db = quote_ident(names.database)
        roles = [quote_ident(r) for r in (names.rw_role, names.ro_role, names.owner_role)]
        stmts = [
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            f" WHERE datname = {quote_literal(names.database)}",  # nosec B608
            f"DROP DATABASE IF EXISTS {db}",
        ]
        if storage_mode == "tablespace":
            stmts.append(f"DROP TABLESPACE IF EXISTS {quote_ident(names.tablespace)}")
        stmts += [f"DROP SCHEMA IF EXISTS {r} CASCADE" for r in roles]
        stmts += [f"DROP ROLE IF EXISTS {r}" for r in roles]
        with self._admin_conn() as conn:
            self._run(conn, stmts)

    def update(self, names: InstanceNames, spec: InstanceParams, storage_mode: str = "role_quota") -> None:
        db = quote_ident(names.database)
        own = quote_ident(names.owner_role)
        stmts = [f"ALTER DATABASE {db} CONNECTION LIMIT = {spec.max_connections}"]
        if storage_mode == "tablespace":
            # Resize the tenant's storage cap. If the new quota is below
            # current usage the change still succeeds, but writes are blocked
            # until usage drops under the new limit.
            stmts.append(
                f"ALTER TABLESPACE {quote_ident(names.tablespace)}"
                f" RESIZE MAXSIZE {quote_literal(f'{spec.storage_gb}GB')}"
            )
        stmts += self._role_quota_stmts(own, spec, include_perm=storage_mode == "role_quota")
        with self._admin_conn() as conn:
            self._run(conn, stmts)
