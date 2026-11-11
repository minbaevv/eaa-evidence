from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from .config import settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with closing(connect()) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error TEXT,
                result_json TEXT,
                summary_json TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_scans_created_at "
            "ON scans(created_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL DEFAULT '',
                max_pages INTEGER NOT NULL DEFAULT 3,
                interval_hours INTEGER NOT NULL DEFAULT 24,
                active INTEGER NOT NULL DEFAULT 1,
                next_run_at TEXT NOT NULL,
                last_scan_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS issue_statuses (
                scan_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scan_id, rule_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_checks (
                scan_id TEXT NOT NULL,
                check_id TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scan_id, check_id)
            )
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(scans)").fetchall()
        }
        if "webhook_url" not in columns:
            connection.execute(
                "ALTER TABLE scans ADD COLUMN webhook_url TEXT NOT NULL DEFAULT ''"
            )

def create_scan(
    scan_id: str,
    url: str,
    company: str,
    contact_email: str,
    webhook_url: str = "",
) -> dict[str, Any]:
    with closing(connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO scans
                (id, url, company, contact_email, webhook_url, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?)
            """,
            (scan_id, url, company, contact_email, webhook_url, utc_now()),
        )
    scan = get_scan(scan_id)
    if scan is None:
        raise RuntimeError("Failed to create scan")
    return scan


def get_scan(scan_id: str) -> dict[str, Any] | None:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT * FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
    return row_to_dict(row) if row is not None else None


def list_scans(limit: int = 10) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM scans ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def update_scan(scan_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "started_at",
        "finished_at",
        "error",
        "result_json",
        "summary_json",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with closing(connect()) as connection, connection:
        connection.execute(
            f"UPDATE scans SET {assignments} WHERE id = ?",
            (*updates.values(), scan_id),
        )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["result"] = (
        json.loads(data["result_json"]) if data.get("result_json") else None
    )
    data["summary"] = (
        json.loads(data["summary_json"]) if data.get("summary_json") else None
    )
    return data


def list_scans_by_url(url: str, limit: int = 20) -> list[dict[str, Any]]:
    """Completed scans for one URL, newest first (regression + trend input)."""
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT * FROM scans
            WHERE url = ? AND status = 'done'
            ORDER BY created_at DESC LIMIT ?
            """,
            (url, limit),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def create_schedule(
    schedule_id: str,
    url: str,
    company: str,
    contact_email: str,
    max_pages: int,
    interval_hours: int,
) -> dict[str, Any] | None:
    now = utc_now()
    with closing(connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO schedules
                (id, url, company, contact_email, max_pages,
                 interval_hours, active, next_run_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (schedule_id, url, company, contact_email, max_pages, interval_hours, now, now),
        )
    return get_schedule(schedule_id)


def get_schedule(schedule_id: str) -> dict[str, Any] | None:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
    return dict(row) if row else None


def list_schedules() -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM schedules ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def due_schedules(now: str) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT * FROM schedules
            WHERE active = 1 AND (next_run_at IS NULL OR next_run_at <= ?)
            ORDER BY next_run_at ASC
            """,
            (now,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_schedule_run(schedule_id: str, scan_id: str, next_run_at: str) -> None:
    with closing(connect()) as connection, connection:
        connection.execute(
            """
            UPDATE schedules
            SET last_scan_id = ?, next_run_at = ?
            WHERE id = ?
            """,
            (scan_id, next_run_at, schedule_id),
        )


def delete_schedule(schedule_id: str) -> None:
    with closing(connect()) as connection, connection:
        connection.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


ISSUE_STATUSES = ("ack", "fixed", "wontfix")


def set_issue_status(scan_id: str, rule_id: str, status: str) -> None:
    if status not in ISSUE_STATUSES:
        raise ValueError(f"Unknown issue status: {status}")
    with closing(connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO issue_statuses (scan_id, rule_id, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (scan_id, rule_id) DO UPDATE
                SET status = excluded.status, updated_at = excluded.updated_at
            """,
            (scan_id, rule_id, status, utc_now()),
        )


def get_issue_statuses(scan_id: str) -> dict[str, str]:
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT rule_id, status FROM issue_statuses WHERE scan_id = ?",
            (scan_id,),
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def set_manual_check(
    scan_id: str, check_id: str, done: bool, note: str = ""
) -> None:
    with closing(connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO manual_checks (scan_id, check_id, done, note, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (scan_id, check_id) DO UPDATE
                SET done = excluded.done, note = excluded.note,
                    updated_at = excluded.updated_at
            """,
            (scan_id, check_id, 1 if done else 0, note[:500], utc_now()),
        )


def get_manual_checks(scan_id: str) -> dict[str, dict[str, Any]]:
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT check_id, done, note FROM manual_checks WHERE scan_id = ?",
            (scan_id,),
        ).fetchall()
    return {
        row[0]: {"done": bool(row[1]), "note": row[2] or ""} for row in rows
    }
