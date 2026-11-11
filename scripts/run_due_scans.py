"""Run due scheduled scans (cron/Task Scheduler friendly).

Usage:
    python scripts/run_due_scans.py          # run all schedules that are due
    python scripts/run_due_scans.py --dry    # list due schedules only

Each due schedule creates a fresh scan and advances next_run_at by its
interval. Configure OS cron / Task Scheduler to call this every few minutes.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import (  # noqa: E402
    create_scan,
    due_schedules,
    init_db,
    mark_schedule_run,
    utc_now,
)
from app.main import run_scan_task  # noqa: E402


def main(dry_run: bool = False) -> int:
    init_db()
    now = datetime.now(timezone.utc)
    due = due_schedules(utc_now())
    print(f"{len(due)} schedule(s) due")
    for schedule in due:
        print(f"- {schedule['url']} (every {schedule['interval_hours']}h)")
        if dry_run:
            continue
        scan_id = uuid.uuid4().hex
        create_scan(
            scan_id,
            schedule["url"],
            schedule["company"] or "",
            schedule["contact_email"] or "",
        )
        run_scan_task(
            scan_id,
            schedule["url"],
            schedule["company"] or "",
            schedule["contact_email"] or "",
            max_pages=schedule["max_pages"] or None,
        )
        next_run = (now + timedelta(hours=schedule["interval_hours"])).isoformat(
            timespec="seconds"
        )
        mark_schedule_run(schedule["id"], scan_id, next_run)
        print(f"  -> scan {scan_id}, next run {next_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(dry_run="--dry" in sys.argv))
