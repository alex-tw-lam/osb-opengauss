"""The offering: loads the plan catalog from a data file, assembles the OSB catalog.

`plans.toml` is DATA - each deployment carries its own copy. This file is the
CODE that loads, validates and assembles it. Validation is strict and loud at
startup so a broken data file can never produce a half-usable catalog.

It does NOT know how requests are validated (params.py), how the quotas reach
openGauss (gaussdb.py) or how OSB endpoints are served (broker.py).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

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

_REQUIRED_FIELDS = ("id", "name", "description", "storage_gb", "temp_gb", "spill_gb", "max_connections")
_QUOTA_FIELDS = ("storage_gb", "temp_gb", "spill_gb", "max_connections")


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
    free: bool = True


def load_plans(path: str) -> tuple[PlanSpec, ...]:
    """Read and validate the plans file; raise ValueError on any problem."""
    file = Path(path)
    if not file.is_file():
        raise ValueError(f"plans file not found: {file}")

    with file.open("rb") as fh:
        rows = tomllib.load(fh).get("plan", [])

    if not rows:
        raise ValueError(f"plans file {file} contains no [[plan]] entries")

    plans = []
    seen_ids = set()
    for position, row in enumerate(rows, start=1):
        missing = set(_REQUIRED_FIELDS) - row.keys()
        if missing:
            raise ValueError(f"plan #{position} in {file} is missing fields: {sorted(missing)}")
        for field in _QUOTA_FIELDS:
            if not isinstance(row[field], int) or row[field] < 1:
                raise ValueError(f"plan {row['id']!r} in {file}: {field} must be a positive integer")
        if row["id"] in seen_ids:
            raise ValueError(f"duplicate plan id {row['id']!r} in {file}")
        seen_ids.add(row["id"])
        plans.append(
            PlanSpec(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                storage_gb=row["storage_gb"],
                temp_gb=row["temp_gb"],
                spill_gb=row["spill_gb"],
                max_connections=row["max_connections"],
                free=bool(row.get("free", True)),
            )
        )
    return tuple(plans)


def build_service(plans: tuple[PlanSpec, ...], tablespaces: tuple = ()) -> Service:
    """Assemble the /v2/catalog payload for the offering."""
    service_plans = [
        ServicePlan(
            id=plan.id,
            name=plan.name,
            description=plan.description,
            free=plan.free,
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
        for plan in plans
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
        plans=service_plans,
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
