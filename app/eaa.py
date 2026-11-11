from __future__ import annotations

from collections import Counter
from typing import Any, Iterable
from urllib.parse import urlparse

EAA_STANDARD = "EN 301 549 / WCAG 2.1 AA"

EAA_SCOPE = [
    "e-commerce",
    "consumer banking",
    "electronic communications",
    "transport booking",
    "e-books and e-readers",
    "digital services offered to consumers in the EU",
]

IMPACT_ORDER = ("critical", "serious", "moderate", "minor")
IMPACT_LABELS = {
    "critical": "Critical",
    "serious": "Serious",
    "moderate": "Moderate",
    "minor": "Minor",
}
IMPACT_WEIGHTS = {
    "critical": 12,
    "serious": 6,
    "moderate": 2,
    "minor": 1,
}

WCAG_TAG_LABELS = {
    "wcag2a": "WCAG 2.0 A",
    "wcag2aa": "WCAG 2.0 AA",
    "wcag21a": "WCAG 2.1 A",
    "wcag21aa": "WCAG 2.1 AA",
    "wcag22aa": "WCAG 2.2 AA",
}

LIMITATIONS = [
    "Automated scanning detects a subset of WCAG issues; it cannot replace manual testing with assistive technology.",
    "A clean automated scan is not a legal certification or a guarantee of EAA conformance.",
    "The scan covers the submitted URL and a small sample of same-origin links, not the entire product or authenticated flows.",
    "Dynamic states, checkout, account areas, PDFs, and native mobile apps require separate review.",
    "This report is evidence for a remediation workflow, not legal advice.",
]

def wcag_tag_label(tag: str) -> str:
    if tag in WCAG_TAG_LABELS:
        return WCAG_TAG_LABELS[tag]
    if tag.startswith("wcag"):
        digits = tag[4:]
        if len(digits) >= 3 and digits.isdigit():
            return f"WCAG {digits[0]}.{digits[1]}.{digits[2:]}"
    return tag


def wcag_labels(issues: Iterable[dict[str, Any]]) -> dict[str, str]:
    labels = dict(WCAG_TAG_LABELS)
    for issue in issues:
        for tag in issue.get("wcag") or []:
            labels[tag] = wcag_tag_label(tag)
    return labels

def en301549_clause(wcag_tag: str) -> str | None:
    """Map an axe-core WCAG tag (e.g. wcag143) to its EN 301 549 clause.

    EN 301 549 chapter 9 mirrors the WCAG criterion number with a 9. prefix:
    WCAG 1.4.3 -> clause 9.1.4.3, WCAG 4.1.2 -> clause 9.4.1.2.
    Level-only tags (wcag2a, wcag21aa, ...) carry no criterion and map to None.
    """
    if not wcag_tag.startswith("wcag"):
        return None
    digits = wcag_tag[4:]
    if len(digits) < 3 or not digits.isdigit():
        return None
    return f"9.{digits[0]}.{digits[1]}.{digits[2:]}"


def en301549_clauses(issue: dict[str, Any]) -> list[str]:
    clauses: list[str] = []
    for tag in issue.get("wcag") or []:
        clause = en301549_clause(tag)
        if clause and clause not in clauses:
            clauses.append(clause)
    return sorted(clauses, key=lambda c: tuple(int(p) for p in c.split(".")))


def normalize_impact(value: str | None) -> str:
    normalized = (value or "minor").lower()
    return normalized if normalized in IMPACT_WEIGHTS else "minor"


def impact_rank(impact: str) -> int:
    try:
        return IMPACT_ORDER.index(normalize_impact(impact))
    except ValueError:
        return len(IMPACT_ORDER)


def sort_issues(issues: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        issues,
        key=lambda issue: (
            impact_rank(issue.get("impact", "minor")),
            issue.get("id", ""),
        ),
    )


def readiness_score(issues: Iterable[dict[str, Any]]) -> int:
    penalty = sum(
        IMPACT_WEIGHTS[normalize_impact(issue.get("impact"))] for issue in issues
    )
    return max(0, 100 - min(100, penalty))


def risk_level(counts: Counter[str] | dict[str, int]) -> str:
    if counts.get("critical", 0) > 0:
        return "High"
    if counts.get("serious", 0) > 0:
        return "Elevated"
    if counts.get("moderate", 0) > 0:
        return "Moderate"
    return "Low"


def summarize_issues(
    issues: list[dict[str, Any]], pages: list[dict[str, Any]]
) -> dict[str, Any]:
    counts: Counter[str] = Counter(
        normalize_impact(issue.get("impact")) for issue in issues
    )
    element_count = sum(int(issue.get("node_count") or 0) for issue in issues)
    return {
        "issue_count": len(issues),
        "element_count": element_count,
        "critical": counts["critical"],
        "serious": counts["serious"],
        "moderate": counts["moderate"],
        "minor": counts["minor"],
        "pages_scanned": len(
            [page for page in pages if page.get("status") == "scanned"]
        ),
        "pages_failed": len(
            [page for page in pages if page.get("status") == "failed"]
        ),
        "readiness_score": readiness_score(issues),
        "risk_level": risk_level(counts),
    }


def top_issues(
    issues: list[dict[str, Any]], limit: int = 5
) -> list[dict[str, Any]]:
    return sort_issues(issues)[:limit]


def build_accessibility_statement(
    company: str,
    url: str,
    scan_date: str,
    contact_email: str,
    issues: list[dict[str, Any]],
    lang: str = "en",
) -> dict[str, Any]:
    display_name = company.strip() or urlparse(url).netloc or url
    ru = lang == "ru"
    contact = contact_email.strip() or (
        "[укажите контактный email]" if ru else "[add a contact email]"
    )
    known_issues = [
        f"{issue.get('help') or issue.get('description') or issue.get('id')} ({issue.get('id')})"
        for issue in top_issues(issues, limit=5)
    ]
    if ru:
        status = (
            "Частично соответствует"
            if issues
            else "Нарушений не выявлено автоматически"
        )
        return {
            "title": f"Декларация доступности для {display_name}",
            "company": display_name,
            "url": url,
            "updated": scan_date,
            "status": status,
            "commitment": (
                f"{display_name} стремится сделать свои цифровые сервисы "
                "доступными для людей с инвалидностью. Мы работаем над устранением "
                "барьеров и улучшением опыта каждого посетителя."
            ),
            "standard": (
                "Мы ориентируемся на WCAG 2.1 уровня AA — техническую базу, "
                "на которую ссылаются EN 301 549 и Европейский закон о доступности (EAA)."
            ),
            "measures": [
                "Автоматические проверки доступности ключевых публичных страниц.",
                "Приоритизированные задачи на исправление для разработки.",
                "Периодическое повторное тестирование после исправлений и перед крупными релизами.",
            ],
            "known_issues": known_issues,
            "feedback": (
                f"Если вы встретили барьер доступности, напишите: {contact}. "
                "Укажите URL страницы и кратко опишите проблему."
            ),
            "enforcement": (
                "Если наш ответ вас не устроил, вы можете обратиться "
                "в национальный орган, ответственный за обеспечение доступности "
                "в стране, где предоставляется услуга."
            ),
            "limitations": (
                f"Этот черновик сформирован по результатам автоматического скана от {scan_date}. "
                "Это стартовая точка для проверки, а не юридическая сертификация."
            ),
        }
    status = "Partially conformant" if issues else "No automated violations detected"
    return {
        "title": f"Accessibility statement for {display_name}",
        "company": display_name,
        "url": url,
        "updated": scan_date,
        "status": status,
        "commitment": (
            f"{display_name} is committed to making its digital services accessible "
            "to people with disabilities. We are working to remove barriers and to "
            "improve the experience of every visitor."
        ),
        "standard": (
            "We aim to conform to WCAG 2.1 Level AA, the technical baseline "
            "referenced by EN 301 549 and the European Accessibility Act (EAA)."
        ),
        "measures": [
            "Automated accessibility checks on key public pages.",
            "Prioritized remediation tickets for engineering.",
            "Periodic re-testing after fixes and before major releases.",
        ],
        "known_issues": known_issues,
        "feedback": (
            f"If you encounter an accessibility barrier, contact {contact}. "
            "Please include the page URL and a short description of the problem."
        ),
        "enforcement": (
            "If you are not satisfied with our response, you can contact the "
            "national authority responsible for enforcing accessibility rules in "
            "the country where the service is offered."
        ),
        "limitations": (
            f"This draft was generated from an automated scan on {scan_date}. "
            "It is a starting point for review, not a legal certification."
        ),
    }



MANUAL_CHECK_IDS: list[str] = [
    "keyboard",
    "screen_reader",
    "zoom_reflow",
    "focus_order",
    "contrast_manual",
    "captions",
    "touch_targets",
    "error_handling",
]
EN301549_CRITERIA: list[tuple[str, str]] = [
    ("9.1.1.1", "Non-text Content"),
    ("9.1.2.1", "Audio-only and Video-only (Prerecorded)"),
    ("9.1.2.2", "Captions (Prerecorded)"),
    ("9.1.2.3", "Audio Description or Media Alternative (Prerecorded)"),
    ("9.1.2.4", "Captions (Live)"),
    ("9.1.2.5", "Audio Description (Prerecorded)"),
    ("9.1.3.1", "Info and Relationships"),
    ("9.1.3.2", "Meaningful Sequence"),
    ("9.1.3.3", "Sensory Characteristics"),
    ("9.1.3.4", "Orientation"),
    ("9.1.3.5", "Identify Input Purpose"),
    ("9.1.4.1", "Use of Color"),
    ("9.1.4.2", "Audio Control"),
    ("9.1.4.3", "Contrast (Minimum)"),
    ("9.1.4.4", "Resize Text"),
    ("9.1.4.5", "Images of Text"),
    ("9.1.4.7", "Low Volume"),
    ("9.1.4.10", "Reflow"),
    ("9.1.4.11", "Non-text Contrast"),
    ("9.1.4.12", "Text Spacing"),
    ("9.1.4.13", "Content on Hover or Focus"),
    ("9.2.1.1", "Keyboard"),
    ("9.2.1.2", "No Keyboard Trap"),
    ("9.2.1.4", "Character Key Shortcuts"),
    ("9.2.2.1", "Timing Adjustable"),
    ("9.2.2.2", "Pause, Stop, Hide"),
    ("9.2.2.4", "Interruptions"),
    ("9.2.3.1", "Three Flashes or Below Threshold"),
    ("9.2.4.1", "Bypass Blocks"),
    ("9.2.4.2", "Page Titled"),
    ("9.2.4.3", "Focus Order"),
    ("9.2.4.4", "Link Purpose (In Context)"),
    ("9.2.4.5", "Multiple Ways"),
    ("9.2.4.6", "Headings and Labels"),
    ("9.2.4.7", "Focus Visible"),
    ("9.2.5.1", "Pointer Gestures"),
    ("9.2.5.2", "Pointer Cancellation"),
    ("9.2.5.3", "Label in Name"),
    ("9.2.5.4", "Motion Actuation"),
    ("9.3.1.1", "Language of Page"),
    ("9.3.1.2", "Language of Parts"),
    ("9.3.2.1", "On Focus"),
    ("9.3.2.2", "On Input"),
    ("9.3.3.1", "Error Identification"),
    ("9.3.3.2", "Labels or Instructions"),
    ("9.3.3.3", "Error Suggestion"),
    ("9.3.3.4", "Error Prevention (Legal, Financial, Data)"),
    ("9.4.1.1", "Parsing"),
    ("9.4.1.2", "Name, Role, Value"),
    ("9.4.1.3", "Status Messages"),
]


def build_vpat(
    company: str,
    url: str,
    scan_date: str,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Draft VPAT/ACR from automated findings (manual review still required)."""
    display_name = company.strip() or urlparse(url).netloc or url
    by_clause: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        for clause in issue.get("en301549") or []:
            by_clause.setdefault(clause, []).append(issue)

    rows: list[dict[str, Any]] = []
    for clause, title in EN301549_CRITERIA:
        found = by_clause.get(clause, [])
        violations = [i for i in found if i.get("kind") == "violations"]
        if violations:
            status = "Does Not Support"
        elif found:
            status = "Partially Supports"
        else:
            status = "Not Evaluated"
        notes = "; ".join(
            f"{issue.get('id')} ({issue.get('node_count', 0)} elements)"
            for issue in found[:4]
        )
        rows.append(
            {
                "clause": clause,
                "title": title,
                "status": status,
                "notes": notes,
            }
        )
    counts = {
        "does_not_support": sum(1 for r in rows if r["status"] == "Does Not Support"),
        "partially": sum(1 for r in rows if r["status"] == "Partially Supports"),
        "not_evaluated": sum(1 for r in rows if r["status"] == "Not Evaluated"),
    }
    return {
        "title": f"VPAT draft for {display_name}",
        "company": display_name,
        "url": url,
        "updated": scan_date,
        "standard": "EN 301 549 / WCAG 2.1 AA (ITIC VPAT 2.4 template, automated draft)",
        "rows": rows,
        "counts": counts,
    }


def rule_ids(result: dict[str, Any]) -> set[str]:
    return {str(issue.get("id")) for issue in result.get("issues") or []}


def compare_reports(
    prev: dict[str, Any], curr: dict[str, Any]
) -> dict[str, Any]:
    """Rule-level regression diff between two scans of the same URL."""
    prev_rules = rule_ids(prev.get("result") or {})
    curr_rules = rule_ids(curr.get("result") or {})
    prev_summary = prev.get("summary") or (prev.get("result") or {}).get("summary") or {}
    curr_summary = curr.get("summary") or (curr.get("result") or {}).get("summary") or {}
    return {
        "new_rules": sorted(curr_rules - prev_rules),
        "resolved_rules": sorted(prev_rules - curr_rules),
        "persistent_rules": sorted(prev_rules & curr_rules),
        "score_before": prev_summary.get("readiness_score"),
        "score_after": curr_summary.get("readiness_score"),
        "since": prev.get("created_at"),
        "since_id": prev.get("id"),
    }
