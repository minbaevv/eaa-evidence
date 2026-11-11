from app.eaa import (
    build_accessibility_statement,
    readiness_score,
    risk_level,
    summarize_issues,
    top_issues,
)


def issue(impact: str, issue_id: str = "test-rule", node_count: int = 1):
    return {
        "id": issue_id,
        "impact": impact,
        "help": f"Fix {issue_id}",
        "description": f"Description for {issue_id}",
        "node_count": node_count,
    }


def test_readiness_score_penalizes_critical_issue():
    assert readiness_score([issue("critical")]) == 88


def test_readiness_score_never_goes_below_zero():
    assert readiness_score([issue("critical") for _ in range(20)]) == 0


def test_risk_level_uses_highest_impact():
    assert risk_level({"critical": 1}) == "High"
    assert risk_level({"serious": 1}) == "Elevated"
    assert risk_level({"moderate": 1}) == "Moderate"
    assert risk_level({}) == "Low"


def test_summarize_issues_counts_pages_and_elements():
    issues = [
        issue("critical", "color-contrast", node_count=4),
        issue("serious", "label", node_count=2),
    ]
    pages = [
        {"status": "scanned"},
        {"status": "failed"},
    ]

    summary = summarize_issues(issues, pages)

    assert summary["issue_count"] == 2
    assert summary["element_count"] == 6
    assert summary["critical"] == 1
    assert summary["serious"] == 1
    assert summary["pages_scanned"] == 1
    assert summary["pages_failed"] == 1
    assert summary["risk_level"] == "High"


def test_top_issues_are_sorted_by_impact():
    issues = [issue("minor", "minor-rule"), issue("critical", "critical-rule")]
    assert top_issues(issues)[0]["id"] == "critical-rule"


def test_statement_contains_contact_and_known_issue():
    statement = build_accessibility_statement(
        company="Acme GmbH",
        url="https://acme.eu",
        scan_date="2026-11-01 12:00 UTC",
        contact_email="accessibility@acme.eu",
        issues=[issue("critical", "color-contrast")],
    )

    assert "accessibility@acme.eu" in statement["feedback"]
    assert "WCAG 2.1" in statement["standard"]
    assert "color-contrast" in statement["known_issues"][0]
    assert statement["status"] == "Partially conformant"
