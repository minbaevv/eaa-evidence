import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.db import (
    create_scan,
    due_schedules,
    get_manual_checks,
    get_issue_statuses,
    init_db,
    list_schedules,
    set_manual_check,
    utc_now,
)
from app.eaa import build_vpat, compare_reports
from app.scanner import _parse_sitemap
from app.tickets import build_tickets, tickets_to_csv


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


def make_done_scan(scan_id="test-scan", url="https://acme.eu/", issues=None):
    scan = create_scan(scan_id, url, "Acme", "a@acme.eu", "")
    from app.db import update_scan

    fake_result = {
        "url": url,
        "company": "Acme",
        "contact_email": "a@acme.eu",
        "scanned_at": "2026-11-01T12:00:20+00:00",
        "axe_version": "4.14.0",
        "pages": [],
        "issues": issues or [],
        "summary": {"readiness_score": 90},
        "limitations": ["Limited."],
        "duration_ms": 10,
    }
    update_scan(
        scan_id,
        status="done",
        result_json=__import__("json").dumps(fake_result),
        summary_json=__import__("json").dumps(fake_result["summary"]),
    )
    return main_module.get_scan(scan_id)


def test_vpat_statuses_derived_from_issues():
    issues = [
        {"id": "color-contrast", "kind": "violations", "wcag": ["wcag143"], "en301549": ["9.1.4.3"], "node_count": 3},
        {"id": "bypass", "kind": "incomplete", "wcag": ["wcag241"], "en301549": ["9.2.4.1"], "node_count": 1},
    ]
    vpat = build_vpat("Acme", "https://acme.eu/", "2026-11-01", issues)
    by_clause = {row["clause"]: row for row in vpat["rows"]}
    assert by_clause["9.1.4.3"]["status"] == "Does Not Support"
    assert by_clause["9.2.4.1"]["status"] == "Partially Supports"
    assert by_clause["9.1.1.1"]["status"] == "Not Evaluated"
    assert vpat["counts"]["does_not_support"] == 1


def test_compare_reports_rule_sets():
    prev = {"id": "a", "created_at": "2026-11-01", "result": {"issues": [{"id": "a"}, {"id": "b"}]}, "summary": {"readiness_score": 80}}
    curr = {"id": "b", "result": {"issues": [{"id": "b"}, {"id": "c"}]}, "summary": {"readiness_score": 88}}
    diff = compare_reports(prev, curr)
    assert diff["new_rules"] == ["c"]
    assert diff["resolved_rules"] == ["a"]
    assert diff["persistent_rules"] == ["b"]


def test_build_tickets_and_csv(client):
    scan = make_done_scan(
        issues=[
            {
                "id": "color-contrast",
                "impact": "critical",
                "help": "Contrast",
                "description": "Fails contrast.",
                "help_url": "https://example.com/rules/color-contrast",
                "wcag": ["wcag2aa", "wcag143"],
                "en301549": ["9.1.4.3"],
                "node_count": 2,
                "nodes": [{"target": "main > p"}],
                "page_url": "https://acme.eu/",
            }
        ]
    )
    tickets = build_tickets(scan)
    assert len(tickets) == 1
    assert tickets[0]["summary"].startswith("[a11y]")
    assert "9.1.4.3" in tickets[0]["body"]
    assert "Acceptance criteria" in tickets[0]["body"]
    csv_text = tickets_to_csv(tickets)
    assert "color-contrast" in csv_text
    assert csv_text.splitlines()[0].startswith("key,summary")


def test_sitemap_parser_filters_files_and_cross_origin():
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://acme.eu/a</loc></url>
      <url><loc>https://acme.eu/img.png</loc></url>
      <url><loc>https://other.eu/b</loc></url>
      <url><loc>https://acme.eu/sitemap-2.xml</loc></url>
    </urlset>"""
    pages, nested = _parse_sitemap(xml, "https://acme.eu/")
    assert pages == ["https://acme.eu/a"]
    assert nested == ["https://acme.eu/sitemap-2.xml"]


def test_issue_status_persists_and_validates(client, monkeypatch):
    monkeypatch.setattr(main_module, "validate_public_url", lambda x: x)
    make_done_scan("status-scan")
    ok = client.post(
        "/scans/status-scan/issues",
        data={"rule_id": "color-contrast", "status": "fixed"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert get_issue_statuses("status-scan")["color-contrast"] == "fixed"
    bad = client.post(
        "/scans/status-scan/issues", data={"rule_id": "x", "status": "bogus"}
    )
    assert bad.status_code == 400


def test_manual_check_persists_and_counts(client):
    make_done_scan("manual-scan")
    response = client.post(
        "/scans/manual-scan/manual",
        data={"check_id": "keyboard", "done": "1", "note": "works"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    state = get_manual_checks("manual-scan")["keyboard"]
    assert state["done"] is True
    assert state["note"] == "works"
    page = client.get("/scans/manual-scan/manual")
    assert page.status_code == 200


def test_schedule_create_list_due_delete(client, monkeypatch):
    stub_browser(monkeypatch)
    created = client.post(
        "/schedules",
        data={"url": "https://acme.eu/", "interval_hours": "6"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    schedules = list_schedules()
    assert len(schedules) == 1
    assert schedules[0]["next_run_at"] <= utc_now()
    assert len(due_schedules(utc_now())) == 1
    page = client.get("/schedules")
    assert "https://example.com" in page.text
    deleted = client.post(
        f"/schedules/{schedules[0]['id']}/delete", follow_redirects=False
    )
    assert deleted.status_code == 303
    assert list_schedules() == []


def test_webhook_url_stored_via_api(client, monkeypatch):
    monkeypatch.setattr(main_module, "validate_public_url", lambda url: url)
    monkeypatch.setattr(main_module, "run_scan_task", lambda *args, **kwargs: None)
    response = client.post(
        "/api/scans",
        json={"url": "https://example.com", "webhook_url": "https://hooks.example.com/x"},
    )
    assert response.status_code == 202
    scan = main_module.get_scan(response.json()["id"])
    assert scan["webhook_url"] == "https://hooks.example.com/x"


def test_vpat_tickets_and_report_pages_render(client):
    make_done_scan(
        "feat-scan",
        issues=[
            {"id": "color-contrast", "kind": "violations", "wcag": ["wcag143"], "en301549": ["9.1.4.3"], "node_count": 1},
        ],
    )
    assert client.get("/scans/feat-scan/vpat").status_code == 200
    assert "§9.1.4.3" in client.get("/scans/feat-scan/vpat").text
    assert "Does Not Support" in client.get("/scans/feat-scan/vpat").text
    tickets = client.get("/scans/feat-scan/tickets")
    assert tickets.status_code == 200
    assert "color-contrast" in tickets.text
    assert client.get("/scans/feat-scan/tickets.json").json()[0]["rule"] == "color-contrast"


def test_report_with_diff_and_trend_renders(client):
    from app.reporting import render_report

    scan = make_done_scan("feat2", issues=[])
    html = render_report(
        scan,
        diff={
            "new_rules": ["color-contrast"],
            "resolved_rules": [],
            "score_before": 88,
            "score_after": 94,
            "since": "2026-11-01",
            "since_id": "prev",
        },
        trend=[
            {"date": "2026-11-01", "score": 88, "id": "prev"},
            {"date": "2026-11-04", "score": 94, "id": "feat2"},
        ],
        statuses={"color-contrast": "fixed"},
        manual={"keyboard": {"done": True, "note": ""}},
    )
    assert "trend-svg" in html
    assert "color-contrast" in html
