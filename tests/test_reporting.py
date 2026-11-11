from app.reporting import render_report, render_statement


def sample_scan():
    issue = {
        "id": "color-contrast",
        "impact": "critical",
        "kind": "violations",
        "description": "Ensures the contrast between foreground and background colors meets WCAG 2 AA contrast ratio thresholds.",
        "help": "Elements must meet minimum color contrast ratio thresholds",
        "help_url": "https://dequeuniversity.com/rules/axe/4.14/color-contrast",
        "tags": ["cat.color", "wcag2aa", "wcag143"],
        "wcag": ["wcag2aa", "wcag143"],
        "page_url": "https://acme.eu/",
        "page_title": "Acme",
        "node_count": 2,
        "nodes": [
            {
                "target": "main > p",
                "html": '<p class="muted">Low contrast</p>',
                "impact": "critical",
                "failure_summary": "Fix any of the following: contrast ratio is 2.1:1.",
            }
        ],
    }
    return {
        "id": "abc123",
        "url": "https://acme.eu/",
        "company": "Acme GmbH",
        "contact_email": "accessibility@acme.eu",
        "status": "done",
        "created_at": "2026-11-01T12:00:00+00:00",
        "finished_at": "2026-11-01T12:00:20+00:00",
        "result": {
            "scanned_at": "2026-11-01T12:00:20+00:00",
            "axe_version": "4.14.0",
            "duration_ms": 20000,
            "pages": [
                {
                    "url": "https://acme.eu/",
                    "title": "Acme",
                    "status": "scanned",
                    "issue_count": 1,
                    "needs_review_count": 0,
                    "duration_ms": 12000,
                }
            ],
            "issues": [issue],
            "limitations": ["Automated scans detect a subset of WCAG issues."],
        },
        "summary": {
            "issue_count": 1,
            "element_count": 2,
            "critical": 1,
            "serious": 0,
            "moderate": 0,
            "minor": 0,
            "pages_scanned": 1,
            "pages_failed": 0,
            "readiness_score": 88,
            "risk_level": "High",
        },
    }


def test_report_renders_issue_and_evidence():
    html = render_report(sample_scan())

    assert "EAA evidence pack" in html
    assert "Acme GmbH" in html
    assert "color-contrast" in html
    assert "Evidence log" in html
    assert "Download PDF" in html


def test_pdf_variant_inlines_css():
    html = render_report(sample_scan(), inline_css=True)

    assert "<style>" in html
    assert "--accent" in html
    assert "Download PDF" not in html


def test_statement_renders_contact_and_known_issue():
    html = render_statement(sample_scan())

    assert "Accessibility statement for Acme GmbH" in html
    assert "accessibility@acme.eu" in html
    assert "color-contrast" in html
    assert "not legal advice" in html
