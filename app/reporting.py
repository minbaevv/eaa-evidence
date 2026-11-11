from __future__ import annotations

from datetime import datetime
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

from .config import BASE_DIR
from .i18n import get_strings, plural
from .eaa import (
    EAA_STANDARD,
    IMPACT_LABELS,
    build_accessibility_statement,
    en301549_clauses,
    top_issues,
    wcag_labels,
)

TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"

environment = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def format_datetime(value: str | None) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


environment.filters["dt"] = format_datetime
environment.globals["plural"] = plural

def _styles() -> str:
    return (STATIC_DIR / "styles.css").read_text(encoding="utf-8")


def render_report(
    scan: dict[str, Any],
    inline_css: bool = False,
    lang: str = "en",
    diff: dict[str, Any] | None = None,
    trend: list[dict[str, Any]] | None = None,
    statuses: dict[str, str] | None = None,
    manual: dict[str, Any] | None = None,
) -> str:
    result = scan.get("result") or {}
    summary = scan.get("summary") or result.get("summary") or {}
    issues = result.get("issues") or []
    for issue in issues:
        issue.setdefault("en301549", en301549_clauses(issue))
    template = environment.get_template("report.html")
    return template.render(
        scan=scan,
        result=result,
        summary=summary,
        issues=issues,
        top=top_issues(issues),
        wcag_labels=wcag_labels(issues),
        standard=EAA_STANDARD,
        inline_css=_styles() if inline_css else None,
        t=get_strings(lang),
        lang=lang,
        diff=diff,
        trend=trend or [],
        statuses=statuses or {},
        manual=manual or {},
    )


def render_statement(scan: dict[str, Any], lang: str = "en") -> str:
    result = scan.get("result") or {}
    statement = build_accessibility_statement(
        company=scan.get("company") or "",
        url=scan.get("url") or "",
        scan_date=format_datetime(result.get("scanned_at")),
        contact_email=scan.get("contact_email") or "",
        issues=result.get("issues") or [],
        lang=lang,
    )
    template = environment.get_template("statement.html")
    return template.render(
        scan=scan, statement=statement, t=get_strings(lang), lang=lang
    )


def render_pdf(html: str) -> bytes:
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        pdf = page.pdf(
            format="A4",
            print_background=True,
            margin={
                "top": "14mm",
                "right": "14mm",
                "bottom": "14mm",
                "left": "14mm",
            },
        )
        browser.close()
        return pdf
