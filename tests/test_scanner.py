import pytest

from app.scanner import ScanError, normalize_url, validate_public_url


def test_normalize_url_adds_https():
    assert normalize_url("example.com") == "https://example.com"


def test_normalize_url_removes_fragment():
    assert normalize_url("https://example.com/path#section") == "https://example.com/path"


def test_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        normalize_url("file:///etc/passwd")


def test_rejects_embedded_credentials():
    with pytest.raises(ValueError):
        normalize_url("https://user:pass@example.com")


def test_rejects_private_ip():
    with pytest.raises(ScanError):
        validate_public_url("http://127.0.0.1", allow_private=False)


def test_rejects_loopback_hostname():
    with pytest.raises(ScanError):
        validate_public_url("http://localhost", allow_private=False)


def test_allows_private_when_explicitly_enabled():
    assert validate_public_url("http://localhost:8000", allow_private=True) == (
        "http://localhost:8000"
    )
