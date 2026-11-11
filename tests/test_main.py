import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.db import init_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.db")
    monkeypatch.setattr(settings, "allow_private_urls", False)
    init_db()
    with TestClient(main_module.app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_home_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Turn an accessibility scan into an evidence pack" in response.text


def test_rejects_private_scan_url(client):
    response = client.post("/api/scans", json={"url": "http://127.0.0.1"})
    assert response.status_code == 400


def test_rejects_non_http_scan_url(client):
    response = client.post("/api/scans", json={"url": "file:///etc/passwd"})
    assert response.status_code == 400


def test_missing_scan_returns_404(client):
    response = client.get("/api/scans/does-not-exist")
    assert response.status_code == 404


def test_missing_scan_page_renders_html_error(client):
    response = client.get("/scans/does-not-exist")
    assert response.status_code == 404
    assert "Scan not found." in response.text


def test_invalid_form_scan_renders_html_error(client):
    response = client.post("/scan", data={"url": "file:///etc/passwd"})
    assert response.status_code == 400
    assert "only http" in response.text.lower()


def test_create_scan_queues_job_without_running_browser(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "validate_public_url", lambda url: "https://example.com"
    )
    monkeypatch.setattr(
        main_module, "run_scan_task", lambda *args, **kwargs: None
    )

    response = client.post(
        "/api/scans",
        json={
            "url": "https://example.com",
            "company": "Example",
            "contact_email": "a11y@example.com",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["id"]

    status = client.get(payload["status_url"])
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
