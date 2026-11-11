import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.db import init_db
from app.i18n import get_strings, plural, resolve_lang
from app.reporting import render_statement
from tests.test_reporting import sample_scan


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


def test_ru_home_renders_russian_ui(client):
    response = client.get("/?lang=ru")
    assert response.status_code == 200
    assert "Превращаем скан доступности в пакет доказательств" in response.text
    assert "Запустить скан" in response.text
    assert 'lang="ru"' in response.text


def test_en_home_stays_english(client):
    response = client.get("/?lang=en")
    assert response.status_code == 200
    assert "Turn an accessibility scan into an evidence pack" in response.text
    assert 'lang="en"' in response.text


def test_language_choice_persists_via_cookie(client):
    chosen = client.get("/?lang=ru")
    assert chosen.cookies.get("eaa_lang") == "ru"
    follow = client.get("/")
    assert "Превращаем скан доступности в пакет доказательств" in follow.text


def test_accept_language_prefers_russian(client):
    response = client.get("/", headers={"Accept-Language": "ru-RU"})
    assert response.status_code == 200
    assert "Превращаем скан доступности в пакет доказательств" in response.text


def test_ru_statement_and_report_render():
    scan = sample_scan()
    statement_ru = render_statement(scan, lang="ru")
    assert "Декларация доступности для Acme GmbH" in statement_ru
    assert "Частично соответствует" in statement_ru
    # Default stays English.
    assert "Accessibility statement for Acme GmbH" in render_statement(scan)


def test_ru_report_renders_russian_labels():
    from app.reporting import render_report

    html = render_report(sample_scan(), lang="ru")
    assert "Пакет доказательств EAA" in html
    assert "Журнал доказательств" in html
    assert "автоматическое правило" in html
    assert "затронуто: 2" in html
    assert "§9.1.4.3" in html  # clause chips are language-independent


def test_plural_rules():
    assert plural("ru", 1, "правило", "правила", "правил") == "правило"
    assert plural("ru", 3, "правило", "правила", "правил") == "правила"
    assert plural("ru", 5, "правило", "правила", "правил") == "правил"
    assert plural("ru", 11, "правило", "правила", "правил") == "правил"
    assert plural("ru", 21, "правило", "правила", "правил") == "правило"
    assert plural("en", 1, "page", "page", "pages") == "page"
    assert plural("en", 2, "page", "page", "pages") == "pages"


def test_get_strings_falls_back_to_en():
    unknown = get_strings("xx")
    assert unknown["hero_h1"] == get_strings("en")["hero_h1"]


def test_resolve_lang_prefers_query_param():
    class FakeRequest:
        query_params = {"lang": "ru"}
        cookies = {"eaa_lang": "en"}
        headers = {"accept-language": "en"}

    assert resolve_lang(FakeRequest()) == "ru"
