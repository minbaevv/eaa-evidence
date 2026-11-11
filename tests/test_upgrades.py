import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.db import create_scan, get_scan, init_db
from app.eaa import en301549_clause, en301549_clauses
from app.reporting import render_report


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.db")
    monkeypatch.setattr(settings, "allow_private_urls", False)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "scan_rate_limit_per_hour", 60)
    main_module._RATE_HITS.clear()
    init_db()
    with TestClient(main_module.app) as test_client:
        yield test_client
    main_module._RATE_HITS.clear()


def stub_browser(monkeypatch):
    monkeypatch.setattr(
        main_module, "validate_public_url", lambda url: "https://example.com"
    )
    monkeypatch.setattr(main_module, "run_scan_task", lambda *args, **kwargs: None)


def make_scan(evidence_hash=None):
    result = {
        "scanned_at": "2026-11-01T12:00:20+00:00",
        "axe_version": "4.14.0",
        "duration_ms": 10,
        "pages": [],
        "issues": [],
        "limitations": ["Limited."],
    }
    if evidence_hash:
        result["evidence_hash"] = evidence_hash
    return {
        "id": "abc123",
        "url": "https://acme.eu/",
        "company": "Acme GmbH",
        "contact_email": "accessibility@acme.eu",
        "status": "done",
        "result": result,
        "summary": {
            "issue_count": 0,
            "element_count": 0,
            "critical": 0,
            "serious": 0,
            "moderate": 0,
            "minor": 0,
            "pages_scanned": 0,
            "pages_failed": 0,
            "readiness_score": 100,
            "risk_level": "Low",
        },
    }


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("wcag111", "9.1.1.1"),
        ("wcag143", "9.1.4.3"),
        ("wcag1412", "9.1.4.12"),
        ("wcag211", "9.2.1.1"),
        ("wcag253", "9.2.5.3"),
        ("wcag412", "9.4.1.2"),
        ("wcag2a", None),
        ("wcag2aa", None),
        ("wcag21a", None),
        ("cat.color", None),
        ("", None),
    ],
)
def test_en301549_clause_mapping(tag, expected):
    assert en301549_clause(tag) == expected


def test_en301549_clauses_dedupes_and_sorts():
    issue = {"wcag": ["wcag412", "wcag2aa", "wcag143", "wcag143"]}
    assert en301549_clauses(issue) == ["9.1.4.3", "9.4.1.2"]


def test_report_shows_en301549_clauses():
    scan = make_scan()
    scan["result"]["issues"] = [
        {
            "id": "color-contrast",
            "impact": "critical",
            "kind": "violations",
            "description": "Contrast.",
            "help": "Contrast help",
            "help_url": "https://example.com",
            "tags": ["wcag2a", "wcag2aa", "wcag143"],
            "wcag": ["wcag2a", "wcag2aa", "wcag143"],
            "page_url": "https://acme.eu/",
            "page_title": "Acme",
            "node_count": 1,
            "nodes": [],
        }
    ]
    html = render_report(scan)
    assert "§9.1.4.3" in html  # issue card chip
    assert "EN 301 549: 9.1.4.3" in html  # priority list inline form


def test_home_renders_page_selector(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="max_pages"' in response.text
    assert 'value="10"' in response.text


def test_form_scan_accepts_max_pages(client, monkeypatch):
    stub_browser(monkeypatch)
    response = client.post(
        "/scan",
        data={"url": "https://example.com", "max_pages": "5"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/scans/")


def test_api_scan_accepts_max_pages(client, monkeypatch):
    stub_browser(monkeypatch)
    response = client.post(
        "/api/scans", json={"url": "https://example.com", "max_pages": 2}
    )
    assert response.status_code == 202


def test_api_scan_rejects_out_of_range_max_pages(client, monkeypatch):
    stub_browser(monkeypatch)
    response = client.post(
        "/api/scans", json={"url": "https://example.com", "max_pages": 99}
    )
    assert response.status_code == 422


def test_api_key_required_when_configured(client, monkeypatch):
    stub_browser(monkeypatch)
    monkeypatch.setattr(settings, "api_key", "top-secret")
    without = client.post("/api/scans", json={"url": "https://example.com"})
    wrong = client.post(
        "/api/scans",
        json={"url": "https://example.com"},
        headers={"X-API-Key": "nope"},
    )
    ok = client.post(
        "/api/scans",
        json={"url": "https://example.com"},
        headers={"X-API-Key": "top-secret"},
    )
    assert without.status_code == 401
    assert wrong.status_code == 401
    assert ok.status_code == 202


def test_rate_limit_returns_429(client, monkeypatch):
    stub_browser(monkeypatch)
    monkeypatch.setattr(settings, "scan_rate_limit_per_hour", 2)
    results = [
        client.post("/api/scans", json={"url": "https://example.com"}).status_code
        for _ in range(3)
    ]
    assert results == [202, 202, 429]


def test_run_scan_task_stores_evidence_hash(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_path", tmp_path / "hash.db")
    init_db()
    fake_result = {
        "url": "https://example.com",
        "company": "",
        "contact_email": "",
        "scanned_at": "2026-11-01T12:00:20+00:00",
        "standard": "EN 301 549 / WCAG 2.1 AA",
        "axe_version": "4.14.0",
        "pages": [],
        "issues": [],
        "summary": {"readiness_score": 100},
        "limitations": ["Limited."],
        "duration_ms": 10,
    }
    monkeypatch.setattr(main_module, "scan_url", lambda *a, **k: dict(fake_result))
    scan_id = "evidence-hash-test"
    create_scan(scan_id, "https://example.com", "", "")
    main_module.run_scan_task(scan_id, "https://example.com", "", "")
    stored = get_scan(scan_id)
    assert stored is not None
    evidence_hash = stored["result"]["evidence_hash"]
    assert len(evidence_hash) == 64
    assert all(c in "0123456789abcdef" for c in evidence_hash)


def test_report_renders_evidence_hash():
    html = render_report(make_scan(evidence_hash="a" * 64))
    assert "Evidence hash" in html
    assert "a" * 64 in html


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("wcag2aa", "WCAG 2.0 AA"),
        ("wcag143", "WCAG 1.4.3"),
        ("wcag241", "WCAG 2.4.1"),
        ("wcag1412", "WCAG 1.4.12"),
        ("wcag412", "WCAG 4.1.2"),
        ("cat.color", "cat.color"),
    ],
)
def test_wcag_tag_label(tag, expected):
    from app.eaa import wcag_tag_label

    assert wcag_tag_label(tag) == expected
