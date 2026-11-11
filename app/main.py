from __future__ import annotations

import json
import logging
import uuid
import hashlib
import hmac
import time
import urllib.request
from .i18n import SUPPORTED_LANGS, get_strings, plural, resolve_lang
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    BackgroundTasks,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import APP_DIR, settings
from .db import (
    ISSUE_STATUSES,
    create_scan,
    create_schedule,
    delete_schedule,
    get_manual_checks,
    get_issue_statuses,
    get_scan,
    init_db,
    list_scans,
    list_scans_by_url,
    list_schedules,
    set_issue_status,
    set_manual_check,
    update_scan,
    utc_now,
)
from .eaa import (
    EAA_SCOPE,
    EAA_STANDARD,
    LIMITATIONS,
    MANUAL_CHECK_IDS,
    build_vpat,
    compare_reports,
)
from .tickets import build_tickets, tickets_to_csv
from .reporting import (
    format_datetime,
    render_pdf,
    render_report,
    render_statement,
)
from .scanner import ScanError, scan_url, validate_public_url

logger = logging.getLogger("eaa_evidence")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.mount(
    "/static",
    StaticFiles(directory=str(APP_DIR / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.filters["dt"] = format_datetime
templates.env.globals["plural"] = plural


class ScanCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    company: str = Field(default="", max_length=120)
    contact_email: str = Field(default="", max_length=200)
    max_pages: int | None = Field(default=None, ge=1, le=25)
    webhook_url: str | None = Field(default=None, max_length=2048)


_RATE_HITS: dict[str, list[float]] = {}


def _parse_max_pages(raw: str) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 1:
        return None
    return min(value, 10)


def require_api_key(request: Request) -> None:
    if not settings.api_key:
        return
    provided = request.headers.get("x-api-key", "")
    if not hmac.compare_digest(provided, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def enforce_rate_limit(request: Request) -> None:
    limit = settings.scan_rate_limit_per_hour
    if limit <= 0:
        return
    client = request.client.host if request.client else "unknown"
    now = time.time()
    window = 3600.0
    hits = [t for t in _RATE_HITS.get(client, []) if now - t < window]
    if len(hits) >= limit:
        retry_after = int(window - (now - hits[0]))
        raise HTTPException(
            status_code=429,
            detail="Too many scan requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)
    _RATE_HITS[client] = hits


_DETAIL_KEYS = {
    "Scan not found.": "err_scan_not_found",
    "Report not found.": "err_report_not_found",
    "Scan is not finished yet.": "err_scan_not_finished",
    "Too many scan requests. Try again later.": "err_rate_limited",
    "Invalid or missing API key.": "err_api_key",
}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    lang = resolve_lang(request)
    t = get_strings(lang)
    key = _DETAIL_KEYS.get(exc.detail)
    detail = t[key] if key else exc.detail
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "status_code": exc.status_code,
            "detail": detail,
            "t": t,
            "lang": lang,
        },
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    lang = resolve_lang(request)
    t = get_strings(lang)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": t["err_validation"], "errors": exc.errors()},
            status_code=422,
        )
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "status_code": 422,
            "detail": t["err_form_invalid"],
            "t": t,
            "lang": lang,
        },
        status_code=422,
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'",
    )
    wanted = (request.query_params.get("lang") or "").lower()[:2]
    if wanted in SUPPORTED_LANGS:
        response.set_cookie(
            "eaa_lang", wanted, path="/", max_age=31_536_000, samesite="lax"
        )
    return response


def _notify_webhook(scan_id: str) -> None:
    scan = get_scan(scan_id)
    if not scan or scan["status"] not in {"done", "failed"}:
        return
    webhook = (scan.get("webhook_url") or "").strip()
    if not webhook:
        return
    payload = {
        "id": scan["id"],
        "status": scan["status"],
        "url": scan["url"],
        "report_url": f"/scans/{scan_id}/report",
        "evidence_hash": (scan.get("result") or {}).get("evidence_hash"),
        "finished_at": scan.get("finished_at"),
    }
    try:
        request = urllib.request.Request(
            webhook,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "EAA-Evidence/0.1 webhook",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read(1024)
    except Exception as exc:  # best-effort delivery
        logger.warning("Webhook delivery failed for %s: %s", scan_id, exc)


def run_scan_task(
    scan_id: str,
    url: str,
    company: str,
    contact_email: str,
    max_pages: int | None = None,
) -> None:
    update_scan(scan_id, status="running", started_at=utc_now())
    try:
        result = scan_url(
            url, company=company, contact_email=contact_email, max_pages=max_pages
        )
        canonical = json.dumps(
            result, sort_keys=True, ensure_ascii=False, default=str
        )
        result["evidence_hash"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        update_scan(
            scan_id,
            status="done",
            finished_at=utc_now(),
            result_json=json.dumps(result, ensure_ascii=False),
            summary_json=json.dumps(result["summary"], ensure_ascii=False),
        )
    except Exception as exc:
        logger.exception("Scan failed for %s", url)
        update_scan(
            scan_id,
            status="failed",
            finished_at=utc_now(),
            error=str(exc)[:1000],
        )
    finally:
        _notify_webhook(scan_id)


def _start_scan(
    background_tasks: BackgroundTasks,
    url: str,
    company: str,
    contact_email: str,
    max_pages: int | None = None,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    try:
        normalized = validate_public_url(url)
    except (ValueError, ScanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    page_budget = None
    if max_pages is not None:
        page_budget = max(1, min(int(max_pages), 25))

    webhook_clean = ""
    if webhook_url:
        try:
            webhook_clean = validate_public_url(webhook_url)
        except (ValueError, ScanError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    scan_id = uuid.uuid4().hex
    scan = create_scan(
        scan_id,
        normalized,
        company.strip()[:120],
        contact_email.strip()[:200],
        webhook_clean,
    )
    background_tasks.add_task(
        run_scan_task, scan_id, normalized, company, contact_email, page_budget
    )
    return scan


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    lang = resolve_lang(request)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "recent": list_scans(5),
            "standard": EAA_STANDARD,
            "scope": EAA_SCOPE,
            "max_pages": settings.max_pages,
            "page_options": list(range(1, 11)),
            "form": {},
            "error": None,
            "t": get_strings(lang),
            "lang": lang,
        },
    )


@app.post("/scan")
def create_scan_form(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    company: str = Form(""),
    contact_email: str = Form(""),
    max_pages: str = Form(""),
):
    enforce_rate_limit(request)
    try:
        scan = _start_scan(
            background_tasks,
            url,
            company,
            contact_email,
            _parse_max_pages(max_pages),
        )
    except HTTPException as exc:
        lang = resolve_lang(request)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "recent": list_scans(5),
                "standard": EAA_STANDARD,
                "scope": EAA_SCOPE,
                "max_pages": settings.max_pages,
                "page_options": list(range(1, 11)),
                "form": {
                    "url": url,
                    "company": company,
                    "contact_email": contact_email,
                    "max_pages": max_pages,
                },
                "error": exc.detail,
                "t": get_strings(lang),
                "lang": lang,
            },
            status_code=exc.status_code,
        )
    return RedirectResponse(url=f"/scans/{scan['id']}", status_code=303)


@app.get("/scans/{scan_id}")
def scan_status(request: Request, scan_id: str):
    scan = get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    if scan["status"] == "done":
        return RedirectResponse(url=f"/scans/{scan_id}/report", status_code=303)
    lang = resolve_lang(request)
    return templates.TemplateResponse(
        request=request,
        name="scan.html",
        context={"scan": scan, "t": get_strings(lang), "lang": lang},
    )


@app.get("/scans/{scan_id}/report", response_class=HTMLResponse)
def scan_report(request: Request, scan_id: str):
    scan = get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    if scan["status"] != "done":
        raise HTTPException(status_code=409, detail="Scan is not finished yet.")
    lang = resolve_lang(request)
    history = list_scans_by_url(scan["url"], limit=20)
    previous = next(
        (item for item in history if item["id"] != scan["id"]), None
    )
    diff = compare_reports(previous, scan) if previous else None
    trend = [
        {
            "date": (item.get("result") or {}).get("scanned_at", "")[:10],
            "score": (item.get("summary") or {}).get("readiness_score"),
            "id": item["id"],
        }
        for item in reversed(history)
        if (item.get("summary") or {}).get("readiness_score") is not None
    ]
    return HTMLResponse(
        render_report(
            scan,
            lang=lang,
            diff=diff,
            trend=trend,
            statuses=get_issue_statuses(scan_id),
            manual=get_manual_checks(scan_id),
        )
    )


@app.get("/scans/{scan_id}/report.html")
def scan_report_html(request: Request, scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    return HTMLResponse(
        render_report(scan, lang=resolve_lang(request)),
        headers={
            "Content-Disposition": f'attachment; filename="eaa-evidence-{scan_id}.html"'
        },
    )


@app.get("/scans/{scan_id}/report.pdf")
def scan_report_pdf(request: Request, scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    pdf = render_pdf(render_report(scan, inline_css=True, lang=resolve_lang(request)))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="eaa-evidence-{scan_id}.pdf"'
        },
    )

@app.get("/scans/{scan_id}/statement", response_class=HTMLResponse)
def accessibility_statement(request: Request, scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    return HTMLResponse(render_statement(scan, lang=resolve_lang(request)))


@app.get("/scans/{scan_id}/result.json")
def scan_result_json(scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    return JSONResponse(scan["result"])


@app.get("/api/scans")
def api_list_scans():
    return [
        {
            "id": scan["id"],
            "url": scan["url"],
            "company": scan["company"],
            "status": scan["status"],
            "created_at": scan["created_at"],
            "summary": scan["summary"],
        }
        for scan in list_scans(20)
    ]


@app.post("/api/scans", status_code=202)
def api_create_scan(
    request: Request, payload: ScanCreate, background_tasks: BackgroundTasks
):
    require_api_key(request)
    enforce_rate_limit(request)
    scan = _start_scan(
        background_tasks,
        payload.url,
        payload.company,
        payload.contact_email,
        payload.max_pages,
        payload.webhook_url,
    )
    return {
        "id": scan["id"],
        "status": scan["status"],
        "status_url": f"/api/scans/{scan['id']}",
        "report_url": f"/scans/{scan['id']}/report",
    }


@app.get("/api/scans/{scan_id}")
def api_scan_status(scan_id: str):
    scan = get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return {
        "id": scan["id"],
        "url": scan["url"],
        "company": scan["company"],
        "status": scan["status"],
        "created_at": scan["created_at"],
        "started_at": scan["started_at"],
        "finished_at": scan["finished_at"],
        "error": scan["error"],
        "summary": scan["summary"],
        "evidence_hash": (
            scan["result"].get("evidence_hash") if scan["result"] else None
        ),
        "report_url": (
            f"/scans/{scan['id']}/report"
            if scan["status"] == "done"
            else None
        ),
    }


@app.get("/api/meta")
def api_meta():
    return {
        "service": settings.app_name,
        "standard": EAA_STANDARD,
        "scope": EAA_SCOPE,
        "limitations": LIMITATIONS,
        "max_pages": settings.max_pages,
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}


@app.get("/scans/{scan_id}/vpat", response_class=HTMLResponse)
def scan_vpat(request: Request, scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    lang = resolve_lang(request)
    result = scan["result"] or {}
    vpat = build_vpat(
        company=scan.get("company") or "",
        url=scan.get("url") or "",
        scan_date=(result.get("scanned_at") or "")[:16].replace("T", " "),
        issues=result.get("issues") or [],
    )
    manual = get_manual_checks(scan_id)
    return templates.TemplateResponse(
        request=request,
        name="vpat.html",
        context={
            "scan": scan,
            "vpat": vpat,
            "manual": manual,
            "manual_done": sum(1 for item in manual.values() if item["done"]),
            "manual_total": len(MANUAL_CHECK_IDS),
            "t": get_strings(lang),
            "lang": lang,
        },
    )


@app.get("/scans/{scan_id}/tickets", response_class=HTMLResponse)
def scan_tickets(request: Request, scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    lang = resolve_lang(request)
    return templates.TemplateResponse(
        request=request,
        name="tickets.html",
        context={
            "scan": scan,
            "tickets": build_tickets(scan),
            "t": get_strings(lang),
            "lang": lang,
        },
    )


@app.get("/scans/{scan_id}/tickets.json")
def scan_tickets_json(scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    return JSONResponse(build_tickets(scan))


@app.get("/scans/{scan_id}/tickets.csv")
def scan_tickets_csv(scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    return Response(
        content=tickets_to_csv(build_tickets(scan)),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="tickets-{scan_id}.csv"'
        },
    )


@app.post("/scans/{scan_id}/issues")
def set_scan_issue_status(
    request: Request, scan_id: str, rule_id: str = Form(...), status: str = Form(...)
):
    if status not in ISSUE_STATUSES:
        raise HTTPException(status_code=400, detail="Unknown issue status.")
    set_issue_status(scan_id, rule_id, status)
    lang = resolve_lang(request)
    return RedirectResponse(url=f"/scans/{scan_id}/report?lang={lang}", status_code=303)


@app.get("/scans/{scan_id}/manual", response_class=HTMLResponse)
def scan_manual(request: Request, scan_id: str):
    scan = get_scan(scan_id)
    if scan is None or scan["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not found.")
    lang = resolve_lang(request)
    checks = get_manual_checks(scan_id)
    return templates.TemplateResponse(
        request=request,
        name="manual.html",
        context={
            "scan": scan,
            "checks": MANUAL_CHECK_IDS,
            "states": checks,
            "done_count": sum(1 for item in checks.values() if item["done"]),
            "t": get_strings(lang),
            "lang": lang,
        },
    )


@app.post("/scans/{scan_id}/manual")
def save_scan_manual(
    request: Request,
    scan_id: str,
    check_id: str = Form(...),
    note: str = Form(""),
    done: str | None = Form(None),
):
    if check_id not in MANUAL_CHECK_IDS:
        raise HTTPException(status_code=400, detail="Unknown manual check.")
    set_manual_check(scan_id, check_id, done is not None, note)
    lang = resolve_lang(request)
    return RedirectResponse(url=f"/scans/{scan_id}/manual?lang={lang}", status_code=303)


@app.get("/schedules", response_class=HTMLResponse)
def list_schedules_page(request: Request):
    lang = resolve_lang(request)
    return templates.TemplateResponse(
        request=request,
        name="schedules.html",
        context={
            "schedules": list_schedules(),
            "page_options": list(range(1, 26)),
            "interval_options": [1, 6, 24, 168],
            "t": get_strings(lang),
            "lang": lang,
        },
    )


@app.post("/schedules")
def create_schedule_route(
    request: Request,
    url: str = Form(...),
    company: str = Form(""),
    contact_email: str = Form(""),
    max_pages: str = Form(""),
    interval_hours: str = Form("24"),
):
    enforce_rate_limit(request)
    try:
        normalized = validate_public_url(url)
    except (ValueError, ScanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    create_schedule(
        uuid.uuid4().hex,
        normalized,
        company.strip()[:120],
        contact_email.strip()[:200],
        _parse_max_pages(max_pages) or settings.max_pages,
        max(1, int(interval_hours) if str(interval_hours).isdigit() else 24),
    )
    lang = resolve_lang(request)
    return RedirectResponse(url=f"/schedules?lang={lang}", status_code=303)


@app.post("/schedules/{schedule_id}/delete")
def delete_schedule_route(request: Request, schedule_id: str):
    delete_schedule(schedule_id)
    lang = resolve_lang(request)
    return RedirectResponse(url=f"/schedules?lang={lang}", status_code=303)
