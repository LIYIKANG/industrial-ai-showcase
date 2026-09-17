from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProjectStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init(self) -> None:
        with self._session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    latest_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS project_versions (
                    project_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (project_id, version)
                );
                CREATE TABLE IF NOT EXISTS chem_materials (
                    project_id TEXT NOT NULL,
                    material_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (project_id, material_id)
                );
                CREATE TABLE IF NOT EXISTS chem_inventory (
                    project_id TEXT NOT NULL,
                    material_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (project_id, material_id)
                );
                CREATE TABLE IF NOT EXISTS chem_inbound (
                    project_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (project_id, item_id)
                );
                CREATE TABLE IF NOT EXISTS chem_open_orders (
                    project_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (project_id, order_id)
                );
                CREATE TABLE IF NOT EXISTS chem_priorities (
                    project_id TEXT NOT NULL,
                    customer_tier TEXT NOT NULL,
                    material_category TEXT NOT NULL,
                    weight REAL NOT NULL,
                    PRIMARY KEY (project_id, customer_tier, material_category)
                );
                CREATE TABLE IF NOT EXISTS chem_bom (
                    project_id TEXT NOT NULL,
                    parent_material_id TEXT NOT NULL,
                    child_material_id TEXT NOT NULL,
                    quantity_per_unit REAL NOT NULL,
                    unit TEXT NOT NULL,
                    yield_ratio REAL,
                    scrap_rate REAL,
                    PRIMARY KEY (project_id, parent_material_id, child_material_id)
                );
                CREATE TABLE IF NOT EXISTS chem_substitutes (
                    project_id TEXT NOT NULL,
                    material_id TEXT NOT NULL,
                    substitute_id TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (project_id, material_id, substitute_id)
                );
                CREATE TABLE IF NOT EXISTS atp_snapshots (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    base_date TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    def list(self) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT id, name, updated_at, latest_version FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def save(self, name: str, payload: dict[str, Any], project_id: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        project_id = project_id or uuid.uuid4().hex
        with self._session() as connection:
            row = connection.execute(
                "SELECT latest_version FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            version = int(row["latest_version"]) + 1 if row else 1
            connection.execute(
                """
                INSERT INTO projects(id, name, updated_at, latest_version)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    updated_at=excluded.updated_at,
                    latest_version=excluded.latest_version
                """,
                (project_id, name.strip() or "未命名项目", now, version),
            )
            connection.execute(
                "INSERT INTO project_versions(project_id, version, created_at, payload) VALUES(?, ?, ?, ?)",
                (project_id, version, now, json.dumps(payload, ensure_ascii=False)),
            )
        return {"id": project_id, "name": name, "version": version, "updated_at": now}

    def get(self, project_id: str, version: int | None = None) -> dict[str, Any] | None:
        with self._session() as connection:
            project = connection.execute(
                "SELECT id, name, updated_at, latest_version FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if not project:
                return None
            target_version = version or int(project["latest_version"])
            snapshot = connection.execute(
                "SELECT version, created_at, payload FROM project_versions WHERE project_id = ? AND version = ?",
                (project_id, target_version),
            ).fetchone()
            versions = connection.execute(
                "SELECT version, created_at FROM project_versions WHERE project_id = ? ORDER BY version DESC",
                (project_id,),
            ).fetchall()
        if not snapshot:
            return None
        return {
            **dict(project),
            "version": snapshot["version"],
            "created_at": snapshot["created_at"],
            "payload": json.loads(snapshot["payload"]),
            "versions": [dict(item) for item in versions],
        }

    # ------------------------------------------------------------------
    # Chemical ATP extensions
    # ------------------------------------------------------------------

    def save_chem_dataset(
        self,
        project_id: str,
        *,
        materials: list[dict[str, Any]] | None = None,
        inventory: list[dict[str, Any]] | None = None,
        inbound: list[dict[str, Any]] | None = None,
        open_orders: list[dict[str, Any]] | None = None,
        priorities: list[dict[str, Any]] | None = None,
        bom: list[dict[str, Any]] | None = None,
        substitutes: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        counts = {"materials": 0, "inventory": 0, "inbound": 0, "open_orders": 0, "priorities": 0, "bom": 0, "substitutes": 0}
        with self._session() as connection:
            for row in materials or []:
                mid = str(row.get("id") or row.get("material_id") or "").strip()
                if not mid:
                    continue
                connection.execute(
                    """
                    INSERT INTO chem_materials(project_id, material_id, payload)
                    VALUES(?, ?, ?)
                    ON CONFLICT(project_id, material_id) DO UPDATE SET payload=excluded.payload
                    """,
                    (project_id, mid, json.dumps(row, ensure_ascii=False)),
                )
                counts["materials"] += 1
            for row in inventory or []:
                mid = str(row.get("material_id") or "").strip() or "_"
                connection.execute(
                    """
                    INSERT INTO chem_inventory(project_id, material_id, payload)
                    VALUES(?, ?, ?)
                    ON CONFLICT(project_id, material_id) DO UPDATE SET payload=excluded.payload
                    """,
                    (project_id, mid, json.dumps(row, ensure_ascii=False)),
                )
                counts["inventory"] += 1
            for row in inbound or []:
                iid = str(row.get("id") or row.get("item_id") or uuid.uuid4().hex)
                connection.execute(
                    """
                    INSERT INTO chem_inbound(project_id, item_id, payload)
                    VALUES(?, ?, ?)
                    ON CONFLICT(project_id, item_id) DO UPDATE SET payload=excluded.payload
                    """,
                    (project_id, iid, json.dumps(row, ensure_ascii=False)),
                )
                counts["inbound"] += 1
            for row in open_orders or []:
                oid = str(row.get("id") or row.get("order_id") or "").strip()
                if not oid:
                    continue
                connection.execute(
                    """
                    INSERT INTO chem_open_orders(project_id, order_id, payload)
                    VALUES(?, ?, ?)
                    ON CONFLICT(project_id, order_id) DO UPDATE SET payload=excluded.payload
                    """,
                    (project_id, oid, json.dumps(row, ensure_ascii=False)),
                )
                counts["open_orders"] += 1
            for row in priorities or []:
                tier = str(row.get("customer_tier") or "").strip().lower()
                cat = str(row.get("material_category") or row.get("category") or "").strip().lower()
                if not tier or not cat:
                    continue
                try:
                    weight = float(row.get("weight") or 0)
                except (TypeError, ValueError):
                    weight = 0.0
                connection.execute(
                    """
                    INSERT INTO chem_priorities(project_id, customer_tier, material_category, weight)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(project_id, customer_tier, material_category) DO UPDATE SET weight=excluded.weight
                    """,
                    (project_id, tier, cat, weight),
                )
                counts["priorities"] += 1
            for row in bom or []:
                parent = str(row.get("parent_material_id") or "").strip()
                child = str(row.get("child_material_id") or "").strip()
                if not parent or not child:
                    continue
                try:
                    qpu = float(row.get("quantity_per_unit") or 0)
                except (TypeError, ValueError):
                    qpu = 0.0
                if qpu <= 0:
                    continue
                unit = str(row.get("unit") or "kg")
                try:
                    yr = float(row.get("yield_ratio")) if row.get("yield_ratio") not in (None, "") else None
                except (TypeError, ValueError):
                    yr = None
                try:
                    sr = float(row.get("scrap_rate")) if row.get("scrap_rate") not in (None, "") else None
                except (TypeError, ValueError):
                    sr = None
                connection.execute(
                    """
                    INSERT INTO chem_bom(project_id, parent_material_id, child_material_id, quantity_per_unit, unit, yield_ratio, scrap_rate)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, parent_material_id, child_material_id) DO UPDATE SET
                        quantity_per_unit=excluded.quantity_per_unit,
                        unit=excluded.unit,
                        yield_ratio=excluded.yield_ratio,
                        scrap_rate=excluded.scrap_rate
                    """,
                    (project_id, parent, child, qpu, unit, yr, sr),
                )
                counts["bom"] += 1
            for row in substitutes or []:
                mid = str(row.get("material_id") or "").strip()
                sid = str(row.get("substitute_id") or "").strip()
                if not mid or not sid:
                    continue
                try:
                    prio = int(row.get("priority") or 0)
                except (TypeError, ValueError):
                    prio = 0
                connection.execute(
                    """
                    INSERT INTO chem_substitutes(project_id, material_id, substitute_id, priority)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(project_id, material_id, substitute_id) DO UPDATE SET priority=excluded.priority
                    """,
                    (project_id, mid, sid, prio),
                )
                counts["substitutes"] += 1
        return counts

    def load_chem_dataset(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        with self._session() as connection:
            materials = [json.loads(r["payload"]) for r in connection.execute(
                "SELECT payload FROM chem_materials WHERE project_id = ?", (project_id,)
            ).fetchall()]
            inventory = [json.loads(r["payload"]) for r in connection.execute(
                "SELECT payload FROM chem_inventory WHERE project_id = ?", (project_id,)
            ).fetchall()]
            inbound = [json.loads(r["payload"]) for r in connection.execute(
                "SELECT payload FROM chem_inbound WHERE project_id = ?", (project_id,)
            ).fetchall()]
            open_orders = [json.loads(r["payload"]) for r in connection.execute(
                "SELECT payload FROM chem_open_orders WHERE project_id = ?", (project_id,)
            ).fetchall()]
            priorities = [dict(r) for r in connection.execute(
                "SELECT customer_tier, material_category, weight FROM chem_priorities WHERE project_id = ?",
                (project_id,),
            ).fetchall()]
            bom = [dict(r) for r in connection.execute(
                "SELECT parent_material_id, child_material_id, quantity_per_unit, unit, yield_ratio, scrap_rate FROM chem_bom WHERE project_id = ?",
                (project_id,),
            ).fetchall()]
            substitutes = [dict(r) for r in connection.execute(
                "SELECT material_id, substitute_id, priority FROM chem_substitutes WHERE project_id = ?",
                (project_id,),
            ).fetchall()]
        return {
            "materials": materials,
            "inventory": inventory,
            "inbound": inbound,
            "open_orders": open_orders,
            "priorities": priorities,
            "bom": bom,
            "substitutes": substitutes,
        }

    def save_atp_snapshot(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        snapshot_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        base_date = str(payload.get("base_date") or now[:10])
        horizon = int(payload.get("horizon_days") or 30)
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO atp_snapshots(id, project_id, name, created_at, base_date, horizon_days, payload)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, project_id, name, now, base_date, horizon, json.dumps(payload, ensure_ascii=False)),
            )
        return {
            "id": snapshot_id,
            "name": name,
            "created_at": now,
            "base_date": base_date,
            "horizon_days": horizon,
        }

    def update_chem_payload(self, project_id: str, **fields: Any) -> bool:
        """Update top-level keys of the latest project version's payload in place.

        Used by the chem bridge to push ``result`` / ``scenarios`` /
        ``validation`` updates back into the project store without
        creating a new version.  Returns ``True`` if the project exists
        and was updated, ``False`` otherwise.
        """
        if not fields:
            return True
        with self._session() as connection:
            row = connection.execute(
                "SELECT latest_version FROM projects WHERE id = ?", (project_id,),
            ).fetchone()
            if not row:
                return False
            payload_row = connection.execute(
                "SELECT payload FROM project_versions WHERE project_id = ? AND version = ?",
                (project_id, int(row["latest_version"])),
            ).fetchone()
            if not payload_row:
                return False
            payload = json.loads(payload_row["payload"])
            payload.update(fields)
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE project_versions SET payload = ?, created_at = ? WHERE project_id = ? AND version = ?",
                (json.dumps(payload, ensure_ascii=False), now, project_id, int(row["latest_version"])),
            )
            connection.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (now, project_id),
            )
        return True

    def list_atp_snapshots(self) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT id, project_id, name, created_at, base_date, horizon_days FROM atp_snapshots ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_atp_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT id, project_id, name, created_at, base_date, horizon_days, payload FROM atp_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            return None
        record = dict(row)
        record["payload"] = json.loads(record["payload"])
        return record
