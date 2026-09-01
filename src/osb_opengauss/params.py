"""Request parameters: validation rules and the matching JSON schemas.

This file answers one question only: what does a valid request look like?

`resolve_instance_params` / `resolve_binding_params` merge user-supplied
parameters over a plan's defaults; parameters may tighten a plan but never
exceed it.  `instance_schema` / `binding_schema` publish the same rules to the
platform as JSON Schema.  This file does NOT talk SQL, openGauss or Flask.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

COMPATIBILITIES = ("PG", "A", "B", "C")  # openGauss DBCOMPATIBILITY
ENCODINGS = ("UTF8", "GBK", "GB18030", "Latin1")  # CREATE DATABASE ENCODING
ACCESS_ROLES = ("owner", "readwrite", "readonly")  # binding access boundary

_JSON_SCHEMA = "http://json-schema.org/draft-04/schema#"


@dataclass(frozen=True)
class InstanceParams:
    """Fully resolved parameters of one logical database."""

    plan_id: str
    compatibility: str = "PG"
    encoding: str = "UTF8"
    tablespace: str | None = None
    max_connections: int = 0  # filled from the plan
    storage_gb: int = 0
    temp_gb: int = 0
    spill_gb: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BindingParams:
    """Fully resolved parameters of one binding user."""

    access_role: str = "readwrite"
    max_connections: int = 0  # filled from the plan

    def as_dict(self) -> dict:
        return asdict(self)


def _bounded_int(params: dict, key: str, default: int, maximum: int) -> int:
    """An integer between 1 and the plan's cap; default when not supplied."""
    value = params.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key!r} must be an integer")
    if value < 1 or value > maximum:
        raise ValueError(f"{key!r} must be between 1 and {maximum}")
    return value


def resolve_instance_params(plan, parameters: dict | None, allowed_tablespaces: tuple = ()) -> InstanceParams:
    params = dict(parameters or {})

    compatibility = params.get("compatibility", "PG")
    if compatibility not in COMPATIBILITIES:
        raise ValueError(f"'compatibility' must be one of {COMPATIBILITIES}")

    encoding = params.get("encoding", "UTF8")
    if encoding not in ENCODINGS:
        raise ValueError(f"'encoding' must be one of {ENCODINGS}")

    tablespace = params.get("tablespace")
    if tablespace is not None:
        if not allowed_tablespaces:
            raise ValueError("'tablespace' is not offered by this broker deployment")
        if tablespace not in allowed_tablespaces:
            raise ValueError(f"'tablespace' must be one of {list(allowed_tablespaces)}")

    return InstanceParams(
        plan_id=plan.id,
        compatibility=compatibility,
        encoding=encoding,
        tablespace=tablespace,
        max_connections=_bounded_int(params, "max_connections", plan.max_connections, plan.max_connections),
        storage_gb=_bounded_int(params, "storage_gb", plan.storage_gb, plan.storage_gb),
        temp_gb=_bounded_int(params, "temp_gb", plan.temp_gb, plan.temp_gb),
        spill_gb=_bounded_int(params, "spill_gb", plan.spill_gb, plan.spill_gb),
    )


def resolve_binding_params(plan, parameters: dict | None) -> BindingParams:
    params = dict(parameters or {})

    access_role = params.get("access_role", "readwrite")
    if access_role not in ACCESS_ROLES:
        raise ValueError(f"'access_role' must be one of {ACCESS_ROLES}")

    return BindingParams(
        access_role=access_role,
        max_connections=_bounded_int(params, "max_connections", plan.max_connections, plan.max_connections),
    )


def instance_schema(plan, tablespaces: tuple) -> dict:
    """JSON Schema for the `parameters` of PUT/PATCH service instances."""
    properties = {
        "compatibility": {
            "type": "string",
            "enum": list(COMPATIBILITIES),
            "default": "PG",
            "description": "openGauss DBCOMPATIBILITY: PG=PostgreSQL, A=Oracle, B=MySQL, C=Teradata.",
        },
        "encoding": {
            "type": "string",
            "enum": list(ENCODINGS),
            "default": "UTF8",
            "description": "Character set of the logical database (LC_COLLATE/LC_CTYPE stay 'C').",
        },
        "max_connections": {
            "type": "integer",
            "minimum": 1,
            "maximum": plan.max_connections,
            "default": plan.max_connections,
            "description": "CONNECTION LIMIT of the logical database.",
        },
        "storage_gb": {
            "type": "integer",
            "minimum": 1,
            "maximum": plan.storage_gb,
            "default": plan.storage_gb,
            "description": "Storage quota of the logical database.",
        },
        "temp_gb": {
            "type": "integer",
            "minimum": 1,
            "maximum": plan.temp_gb,
            "default": plan.temp_gb,
            "description": "Temp-table space quota (TEMP SPACE).",
        },
        "spill_gb": {
            "type": "integer",
            "minimum": 1,
            "maximum": plan.spill_gb,
            "default": plan.spill_gb,
            "description": "Operator spill-to-disk quota (SPILL SPACE).",
        },
    }
    # Only operator-curated tablespaces are offered, as an enum.
    if tablespaces:
        properties["tablespace"] = {
            "type": "string",
            "enum": list(tablespaces),
            "description": "Existing tablespace for the logical database (default pg_default).",
        }
    # Plan changes are not supported; updates may only tweak these parameters.
    updatable = {
        k: dict(v)
        for k, v in properties.items()
        if k in ("max_connections", "storage_gb", "temp_gb", "spill_gb")
    }
    return {
        "create": {"parameters": {"$schema": _JSON_SCHEMA, "type": "object", "properties": properties}},
        "update": {"parameters": {"$schema": _JSON_SCHEMA, "type": "object", "properties": updatable}},
    }


def binding_schema(plan) -> dict:
    """JSON Schema for the `parameters` of PUT service bindings."""
    return {
        "create": {
            "parameters": {
                "$schema": _JSON_SCHEMA,
                "type": "object",
                "properties": {
                    "access_role": {
                        "type": "string",
                        "enum": list(ACCESS_ROLES),
                        "default": "readwrite",
                        "description": "Access boundary of the binding user: owner (full DDL+DML), "
                        "readwrite (DML on the tenant schema), readonly (SELECT only).",
                    },
                    "max_connections": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": plan.max_connections,
                        "default": plan.max_connections,
                        "description": "Per-user CONNECTION LIMIT.",
                    },
                },
            }
        }
    }
