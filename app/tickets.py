from __future__ import annotations

import csv
import io
import json
from typing import Any

from .eaa import en301549_clauses, top_issues

SEVERITY_BY_IMPACT = {
    "critical": "Critical",
    "serious": "High",
    "moderate": "Medium",
    "minor": "Low",
}


def build_tickets(scan: dict[str, Any]) -> list[dict[str, Any]]:
    """One engineering ticket per axe rule found in a scan."""
    result = scan.get("result") or {}
    summary = scan.get("summary") or {}
    scan_date = result.get("scanned_at") or scan.get("created_at") or ""
    origin = scan.get("url") or result.get("url") or ""
    tickets: list[dict[str, Any]] = []
    for issue in top_issues(result.get("issues") or [], limit=100):
        clauses = issue.get("en301549") or en301549_clauses(issue)
        nodes = issue.get("nodes") or []
        sample_targets = "\n".join(f"- `{node.get('target')}`" for node in nodes[:5])
        body = "\n".join(
            [
                f"**Rule:** `{issue.get('id')}`",
                f"**EN 301 549 clauses:** {', '.join(clauses) or '—'}",
                f"**Affected elements:** {issue.get('node_count', 0)}",
                f"**Found on (scan {scan.get('id')}, {scan_date}):** {origin}",
                "",
                (issue.get("description") or "").strip(),
                "",
                "### Affected element samples" if sample_targets else "",
                sample_targets,
                "",
                "### Acceptance criteria",
                "- [ ] The axe-core rule `{}` passes with zero violations on the pages listed above.".format(
                    issue.get("id")
                ),
                "- [ ] Manual keyboard/screen-reader check for the affected components.",
                "- [ ] Re-run the scan and confirm the rule no longer appears.",
                "",
                f"Rule documentation: {issue.get('help_url')}",
            ]
        ).strip()
        tickets.append(
            {
                "key": f"A11Y-{len(tickets) + 1:03d}",
                "summary": (
                    f"[a11y] {issue.get('help') or issue.get('id')} "
                    f"({issue.get('id')})"
                ),
                "impact": issue.get("impact", "minor"),
                "severity": SEVERITY_BY_IMPACT.get(issue.get("impact", "minor"), "Low"),
                "rule": issue.get("id"),
                "en301549": ", ".join(clauses),
                "affected_elements": issue.get("node_count", 0),
                "page": issue.get("page_url"),
                "labels": ["accessibility", "eaa", f"impact-{issue.get('impact', 'minor')}"],
                "body": body,
                "readiness_score": summary.get("readiness_score"),
            }
        )
    return tickets


def tickets_to_json(tickets: list[dict[str, Any]]) -> str:
    return json.dumps(tickets, ensure_ascii=False, indent=2)


def tickets_to_csv(tickets: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    fieldnames = [
        "key",
        "summary",
        "severity",
        "rule",
        "en301549",
        "affected_elements",
        "page",
        "labels",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for ticket in tickets:
        row = {name: ticket.get(name, "") for name in fieldnames}
        row["labels"] = ";".join(ticket.get("labels") or [])
        writer.writerow(row)
    return buffer.getvalue()
