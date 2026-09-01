"""Memory: remembers what the broker created (SQLite tables only).

The broker must answer repeated PUT/DELETE calls in a platform-friendly way
(200 for identical requests, 409 for conflicts, 410 for already-gone), so it
needs to remember what it created.  This file is that memory and nothing
else: plain INSERT/SELECT/UPDATE/DELETE, no SQL DDL for openGauss, no OSB
knowledge.
"""

from __future__ import annotations

import json
import sqlite3
import threading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    instance_id TEXT PRIMARY KEY,
    service_id  TEXT NOT NULL,
    plan_id     TEXT NOT NULL,
    db_name     TEXT NOT NULL,
    params_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS bindings (
    binding_id      TEXT PRIMARY KEY,
    instance_id     TEXT NOT NULL REFERENCES instances(instance_id),
    username        TEXT NOT NULL,
    group_role      TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    credentials_json TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_bindings_instance ON bindings(instance_id);
"""


class StateStore:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- instances -----------------------------------------------------------

    def put_instance(
        self, instance_id: str, service_id: str, plan_id: str, db_name: str, params: dict
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO instances (instance_id, service_id, plan_id, db_name, params_json)"
                " VALUES (?,?,?,?,?)",
                (instance_id, service_id, plan_id, db_name, json.dumps(params, sort_keys=True)),
            )
            self._conn.commit()

    def get_instance(self, instance_id: str) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM instances WHERE instance_id = ?", (instance_id,)
            ).fetchone()
        return row

    def update_instance_params(self, instance_id: str, params: dict) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE instances SET params_json = ? WHERE instance_id = ?",
                (json.dumps(params, sort_keys=True), instance_id),
            )
            self._conn.commit()

    def delete_instance(self, instance_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM instances WHERE instance_id = ?", (instance_id,))
            self._conn.commit()

    # -- bindings ------------------------------------------------------------

    def put_binding(
        self,
        binding_id: str,
        instance_id: str,
        username: str,
        group_role: str,
        params: dict,
        credentials: dict,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO bindings (binding_id, instance_id, username, group_role,"
                " params_json, credentials_json) VALUES (?,?,?,?,?,?)",
                (
                    binding_id,
                    instance_id,
                    username,
                    group_role,
                    json.dumps(params, sort_keys=True),
                    json.dumps(credentials, sort_keys=True),
                ),
            )
            self._conn.commit()

    def get_binding(self, binding_id: str) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM bindings WHERE binding_id = ?", (binding_id,)).fetchone()
        return row

    def list_bindings_for_instance(self, instance_id: str) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bindings WHERE instance_id = ?", (instance_id,)
            ).fetchall()
        return rows

    def delete_binding(self, binding_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bindings WHERE binding_id = ?", (binding_id,))
            self._conn.commit()
