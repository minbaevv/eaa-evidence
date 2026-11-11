from __future__ import annotations

import ipaddress
import socket
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .config import settings
from .eaa import (
    EAA_STANDARD,
    LIMITATIONS,
    en301549_clauses,
    normalize_impact,
    sort_issues,
    summarize_issues,
)

USER_AGENT = "EAA-Evidence/0.1 (+automated accessibility evidence)"
SKIP_LINK_PATTERNS = (
    "logout",
    "signout",
    "sign-out",
    "delete",
    "remove",
    "unsubscribe",
    "cart/delete",
)


class ScanError(RuntimeError):
    pass


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("URL is required.")
    if len(raw) > 2048:
        raise ValueError("URL is too long.")
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// URLs are supported.")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname.")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed.")

    return parsed._replace(fragment="").geturl()


def _is_private_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(raw: str, allow_private: bool | None = None) -> str:
    url = normalize_url(raw)
    if allow_private is None:
        allow_private = settings.allow_private_urls
    if allow_private:
        return url

    host = urlparse(url).hostname
    if not host:
        raise ValueError("URL must include a hostname.")

    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ScanError(f"Could not resolve host: {host}") from exc

    if not addresses:
        raise ScanError(f"Could not resolve host: {host}")

    for address in addresses:
        if _is_private_ip(address[4][0]):
            raise ScanError(
                "Private, loopback, and reserved addresses are not allowed."
            )
    return url


def _same_origin(candidate: str, origin: str) -> bool:
    candidate_parsed = urlparse(candidate)
    origin_parsed = urlparse(origin)
    return (
        candidate_parsed.scheme in {"http", "https"}
        and candidate_parsed.netloc == origin_parsed.netloc
    )


def _looks_risky(href: str) -> bool:
    lower = href.lower()
    return any(pattern in lower for pattern in SKIP_LINK_PATTERNS)


def _collect_links(page: Page, origin: str, limit: int) -> list[str]:
    if limit <= 0:
        return []

    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    links: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if not isinstance(href, str):
            continue
        clean = href.split("#", 1)[0]
        if not clean or clean in seen:
            continue
        if not _same_origin(clean, origin) or _looks_risky(clean):
            continue
        seen.add(clean)
        links.append(clean)
        if len(links) >= limit:
            break
    return links


_FILE_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".pdf",
    ".zip", ".gz", ".mp4", ".mp3", ".wav", ".ogg", ".mov", ".woff", ".woff2",
)


def _parse_sitemap(xml_text: str, origin: str) -> tuple[list[str], list[str]]:
    """Return (page urls, nested sitemap urls) from sitemap XML."""
    pages: list[str] = []
    nested: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return pages, nested
    for loc in root.iter():
        if not loc.tag.endswith("loc") or not (loc.text or "").strip():
            continue
        url = loc.text.strip().split("#", 1)[0]
        if not url or not _same_origin(url, origin) or _looks_risky(url):
            continue
        lower = url.lower()
        if lower.endswith(_FILE_SUFFIXES):
            continue
        if lower.endswith(".xml") or "sitemap" in lower:
            nested.append(url)
        else:
            pages.append(url)
    return pages, nested


def _fetch_sitemap_urls(origin: str, max_sitemaps: int = 3) -> list[str]:
    """Best-effort: sitemap.xml locations and robots.txt Sitemap: lines."""
    origin_parsed = urlparse(origin)
    base = f"{origin_parsed.scheme}://{origin_parsed.netloc}"
    sitemap_urls = [base + "/sitemap.xml"]
    try:
        robots = urllib.request.urlopen(
            base + "/robots.txt", timeout=5
        ).read().decode("utf-8", errors="ignore")
        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap_urls.append(urljoin(base, line.split(":", 1)[1].strip()))
    except Exception:
        pass

    pages: list[str] = []
    seen: set[str] = set()
    queue = list(sitemap_urls[:max_sitemaps])
    while queue:
        sitemap_url = queue.pop(0)
        try:
            validate_public_url(sitemap_url)
            raw = urllib.request.urlopen(
                urllib.request.Request(
                    sitemap_url, headers={"User-Agent": USER_AGENT}
                ),
                timeout=5,
            ).read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        found, nested = _parse_sitemap(raw, origin)
        for url in found:
            if url not in seen:
                seen.add(url)
                pages.append(url)
        queue.extend(u for u in nested if u not in sitemap_urls)
    return pages


def _normalize_results(
    axe_results: dict[str, Any], page_url: str, page_title: str
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for kind in ("violations", "incomplete"):
        for item in axe_results.get(kind, []):
            raw_nodes = item.get("nodes") or []
            nodes = []
            for node in raw_nodes[: settings.max_nodes_per_issue]:
                nodes.append(
                    {
                        "target": " ".join(node.get("target") or []),
                        "html": (node.get("html") or "")[:2000],
                        "impact": normalize_impact(
                            node.get("impact") or item.get("impact")
                        ),
                        "failure_summary": (node.get("failureSummary") or "")[:2000],
                    }
                )

            tags = item.get("tags") or []
            wcag_tags = [tag for tag in tags if tag.startswith("wcag")]
            issues.append(
                {
                    "id": item.get("id", "unknown"),
                    "impact": normalize_impact(item.get("impact")),
                    "kind": kind,
                    "description": item.get("description", ""),
                    "help": item.get("help", ""),
                    "help_url": item.get("helpUrl", ""),
                    "tags": tags,
                    "wcag": wcag_tags,
                    "en301549": en301549_clauses({"wcag": wcag_tags}),
                    "page_title": page_title,
                    "node_count": len(raw_nodes),
                    "nodes": nodes,
                }
            )
    return sort_issues(issues)


def _scan_page(page: Page, url: str) -> dict[str, Any]:
    started = time.perf_counter()
    response = page.goto(
        url, wait_until="domcontentloaded", timeout=settings.scan_timeout_ms
    )
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass

    final_url = page.url
    validate_public_url(final_url, allow_private=settings.allow_private_urls)
    title = page.title()
    axe_source = settings.axe_path.read_text(encoding="utf-8")
    page.evaluate(axe_source)
    axe_results = page.evaluate(
        """
        async () => {
          return await axe.run(document, {
            runOnly: {
              type: 'tag',
              values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']
            },
            resultTypes: ['violations', 'incomplete']
          });
        }
        """
    )
    issues = _normalize_results(axe_results, final_url, title)
    axe_version = page.evaluate("axe.version")

    return {
        "url": final_url,
        "title": title,
        "status": "scanned",
        "http_status": response.status if response is not None else None,
        "issue_count": len(
            [issue for issue in issues if issue["kind"] == "violations"]
        ),
        "needs_review_count": len(
            [issue for issue in issues if issue["kind"] == "incomplete"]
        ),
        "issues": issues,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "axe_version": axe_version,
    }


def _route_handler():
    cache: dict[str, bool] = {}

    def is_blocked(host: str) -> bool:
        if host in cache:
            return cache[host]
        try:
            addresses = socket.getaddrinfo(host, None)
        except socket.gaierror:
            cache[host] = True
            return True
        blocked = any(_is_private_ip(address[4][0]) for address in addresses)
        cache[host] = blocked
        return blocked

    def handler(route):
        parsed = urlparse(route.request.url)
        if parsed.scheme in {"data", "blob", "about"}:
            route.continue_()
            return
        if parsed.scheme not in {"http", "https"}:
            route.abort()
            return
        host = parsed.hostname
        if not host or is_blocked(host):
            route.abort()
            return
        route.continue_()

    return handler


def _launch_browser(playwright: Any):
    configured = settings.browser_channel.strip()
    candidates: list[str | None] = []
    if configured:
        candidates.append(configured)
    if sys.platform == "win32":
        candidates.extend(["msedge", "chrome", None])
    else:
        candidates.append(None)

    errors: list[str] = []
    seen: set[str] = set()
    for channel in candidates:
        key = channel or ""
        if key in seen:
            continue
        seen.add(key)
        browser = None
        try:
            browser = playwright.chromium.launch(headless=True, channel=channel)
            probe = browser.new_page()
            probe.close()
            return browser
        except Exception as exc:
            errors.append(f"{channel or 'bundled'}: {exc}")
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    raise ScanError(
        "Could not start a usable browser. Tried "
        + "; ".join(errors)
        + ". Run `python -m playwright install chromium` or set BROWSER_CHANNEL."
    )


def scan_url(
    raw_url: str,
    company: str = "",
    contact_email: str = "",
    max_pages: int | None = None,
) -> dict[str, Any]:
    url = validate_public_url(raw_url)
    page_limit = max(1, min(int(max_pages or settings.max_pages), 25))
    started = time.perf_counter()
    pages: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            user_agent=USER_AGENT,
        )
        context.set_default_timeout(settings.scan_timeout_ms)
        if not settings.allow_private_urls:
            context.route("**/*", _route_handler())

        page = context.new_page()
        try:
            first = _scan_page(page, url)
            all_issues.extend(first.pop("issues"))
            pages.append(first)

            sitemap_urls = _fetch_sitemap_urls(url, max_sitemaps=3)
            links = sitemap_urls[: max(0, page_limit - 1)] or _collect_links(
                page, url, page_limit - 1
            )
            seen_links = {url}
            queue: list[str] = []
            for link in links:
                if link not in seen_links:
                    seen_links.add(link)
                    queue.append(link)
            if len(queue) < page_limit - 1:
                for link in _collect_links(page, url, page_limit - 1):
                    if link not in seen_links:
                        seen_links.add(link)
                        queue.append(link)

            for link in queue[: max(0, page_limit - 1)]:
                try:
                    result = _scan_page(page, link)
                    all_issues.extend(result.pop("issues"))
                    pages.append(result)
                except Exception as exc:
                    pages.append(
                        {
                            "url": link,
                            "title": "",
                            "status": "failed",
                            "error": str(exc)[:500],
                            "issue_count": 0,
                            "needs_review_count": 0,
                            "duration_ms": 0,
                            "axe_version": None,
                        }
                    )
        finally:
            context.close()
            browser.close()

    summary = summarize_issues(all_issues, pages)
    return {
        "url": url,
        "company": company,
        "contact_email": contact_email,
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "standard": EAA_STANDARD,
        "axe_version": pages[0].get("axe_version") if pages else None,
        "pages": pages,
        "issues": all_issues,
        "summary": summary,
        "limitations": LIMITATIONS,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
