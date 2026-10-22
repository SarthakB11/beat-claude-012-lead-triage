"""The plain-code rule layer.

Every rule returns RuleHit objects. A hit carries an action:

* "escalate": the row goes to a human; the model never sees it.
* "reject":   the row is closed by code (test data, duplicate); the model never sees it.
* "flag":     the row still goes to the model, the finding is passed to it as
              context, and its confidence is capped.

Routing precedence (see route()): manipulation and sensitive content first,
then test data, then the other escalation rules, then duplicates.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import detector
from .schema import (
    REQUIRED_FIELDS,
    Lead,
    email_domain,
    normalise_company,
    normalise_email,
    normalise_name,
    website_domain,
)

FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
    "aol.com", "icloud.com", "me.com", "protonmail.com", "proton.me", "gmx.com", "gmx.de",
    "yandex.com", "mail.com", "zoho.com",
}
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
    "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com",
    "maildrop.cc", "throwawaymail.com", "fakeinbox.com",
}
# Reserved or conventional placeholder domains (RFC 2606 plus common stand-ins).
PLACEHOLDER_DOMAIN = re.compile(
    r"^(example\.[a-z.]+|[a-z0-9-]+\.example|[a-z0-9-]+\.test|[a-z0-9-]+\.invalid|"
    r"test\.com|domain\.com|email\.com|asdf\.com|foo\.com|bar\.com)$"
)
TEST_WORDS = {"test", "testing", "asdf", "qwerty", "foo", "bar", "foobar", "xxx", "abc",
              "n/a", "na", "none", "null", "lorem", "ipsum", "dummy", "sample"}
KEYBOARD_MASH = re.compile(r"(asdf|qwer|zxcv|sdfg|dfgh|hjkl|uiop)", re.I)
REPEATED_CHUNK = re.compile(r"^(\w{2,6})\1+$", re.I)
SENTINEL_BUDGETS = {99999.0, 999999.0, 9999999.0, 123456.0, 1234567.0}
EMAIL_SYNTAX = re.compile(r"^[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")

SENSITIVE: dict[str, re.Pattern[str]] = {
    "legal": re.compile(
        r"\b(lawsuit|legal action|attorneys?|lawyers?|sue|suing|cease and desist|litigation|"
        r"subpoena|court order)\b", re.I),
    "privacy": re.compile(
        r"\b(gdpr|ccpa|article 17|right to be forgotten|personal data|data subject|"
        r"delete (all )?(my|our) (personal )?(data|information)|erase my)\b", re.I),
    "payment": re.compile(
        r"\b(invoice|overdue|wire transfer|bank account|iban|routing number|credit card|"
        r"card number|cvv|payment details|refund)\b", re.I),
    "third_party_personal_data": re.compile(
        r"\b(social security|ssn|passport number|date of birth|medical records?|patient data|"
        r"hipaa)\b", re.I),
    "security": re.compile(
        r"(https?://\S+\.(exe|scr|msi|bat|cmd|js|vbs|apk|iso|dmg|zip|rar)\b|"
        r"\bdownload the attached\b|\bavoid service interruption\b|\bverify your account\b)", re.I),
}

EMPLOYEE_COUNT = re.compile(r"(\d[\d,.]*)\s*\+?\s*[- ]?(employees?|staff|people|person)\b", re.I)
BIG_COMPANY_CLAIM = re.compile(
    r"\b(fortune\s*(500|1000)|unlimited budget|enterprise|multinational|global brand)\b", re.I)
SMALL_COMPANY_CLAIM = re.compile(r"\b(pre-seed|bootstrapped|solo founder|side project|small budget)\b", re.I)

LOW_BUDGET_FOR_BIG_COMPANY = 1_000  # USD per month
HIGH_VALUE_BUDGET = 10_000  # USD per month
IMPLAUSIBLE_BUDGET = 500_000  # USD per month


@dataclass(frozen=True)
class RuleHit:
    code: str
    action: str  # "escalate" | "reject" | "flag"
    detail: str  # one line, safe to put in a reason
    quote: str | None = None  # raw lead text, only ever printed inside a fenced block
    heuristic: bool = False  # True when the rule can misfire on a legitimate lead


# ----------------------------------------------------------------- per-row rules


def check_required(lead: Lead) -> list[RuleHit]:
    hits = []
    for name in REQUIRED_FIELDS:
        value = lead.get(name)
        if name == "message":
            value = lead.clean_message
        if not value:
            hits.append(RuleHit(f"missing_{name}", "escalate", f"required field '{name}' is empty"))
    return hits


def check_timestamp(lead: Lead, now: datetime) -> list[RuleHit]:
    value = lead.get("submitted_at")
    if not value:
        return []
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return [RuleHit("malformed_timestamp", "escalate",
                        f"submitted_at '{value}' is not a valid date and time")]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if parsed > now + timedelta(days=1):
        return [RuleHit("future_timestamp", "escalate", f"submitted_at '{value}' is in the future")]
    return []


def check_email(lead: Lead) -> list[RuleHit]:
    value = lead.get("email")
    if not value:
        return []
    if not EMAIL_SYNTAX.match(value):
        return [RuleHit("invalid_email", "escalate", f"email '{value}' is not a valid address")]
    domain = email_domain(value)
    if domain in DISPOSABLE_DOMAINS:
        return [RuleHit("disposable_email", "escalate",
                        f"email uses the disposable inbox domain {domain}")]
    if PLACEHOLDER_DOMAIN.match(domain):
        return [RuleHit("placeholder_email", "escalate",
                        f"email uses the placeholder domain {domain}")]
    return []


def check_budget(lead: Lead) -> list[RuleHit]:
    value, note = lead.budget_value, lead.budget_note
    if note == "empty":
        return [RuleHit("budget_missing", "flag", "monthly budget is blank")]
    if value is None:
        return [RuleHit("budget_not_numeric", "flag",
                        f"monthly budget is {note}; treated as unknown")]
    hits = []
    if note:
        hits.append(RuleHit("budget_shorthand", "flag", f"monthly budget {note}"))
    if value < 0:
        hits.append(RuleHit("negative_budget", "escalate", f"monthly budget is negative ({value:.0f})"))
    elif value >= IMPLAUSIBLE_BUDGET:
        hits.append(RuleHit("implausible_budget", "escalate",
                            f"monthly budget {value:.0f} USD is implausibly high for an inbound form"))
    return hits


def check_website(lead: Lead) -> list[RuleHit]:
    site = lead.get("website")
    if not site:
        return []
    domain = website_domain(site)
    hits = []
    if "." not in domain or " " in domain:
        hits.append(RuleHit("invalid_website", "flag", f"website '{site}' is not a valid URL"))
        return hits
    mail_domain = email_domain(lead.get("email"))
    if mail_domain and mail_domain not in FREE_MAIL_DOMAINS and not (
        mail_domain.endswith(domain) or domain.endswith(mail_domain)
    ):
        hits.append(RuleHit("domain_mismatch", "flag",
                            f"email domain {mail_domain} differs from website domain {domain}",
                            heuristic=True))
    return hits


def check_markup(lead: Lead) -> list[RuleHit]:
    raw = lead.get("message")
    if raw and raw.strip() != lead.clean_message and re.search(r"<[a-z/!][^>]*>|&[a-z]+;", raw, re.I):
        return [RuleHit("markup_in_message", "flag",
                        "message contained HTML tags or entities; the model sees a cleaned copy")]
    return []


def check_sensitive(lead: Lead) -> list[RuleHit]:
    text = " ".join([lead.get("message"), lead.get("company"), lead.get("name")])
    hits = []
    for category, pattern in SENSITIVE.items():
        match = pattern.search(text)
        if match:
            hits.append(RuleHit(f"sensitive_{category}", "escalate",
                                f"{category.replace('_', ' ')} content ('{match.group(0)}')",
                                quote=detector.make_visible(lead.get("message"))))
    return hits


def check_conflicts(lead: Lead) -> list[RuleHit]:
    hits = []
    text = lead.clean_message
    budget = lead.budget_value
    employees = None
    match = EMPLOYEE_COUNT.search(text)
    if match:
        try:
            employees = int(float(match.group(1).replace(",", "")))
        except ValueError:
            employees = None
    big_claim = BIG_COMPANY_CLAIM.search(text)
    if budget is not None and budget < LOW_BUDGET_FOR_BIG_COMPANY and (
        (employees is not None and employees >= 1000) or big_claim
    ):
        what = f"{employees:,} employees" if employees and employees >= 1000 else f"'{big_claim.group(0)}'"
        hits.append(RuleHit("conflict_budget_vs_size", "escalate",
                            f"stated budget {budget:.0f} USD/month contradicts {what}",
                            heuristic=True))
    small_claim = SMALL_COMPANY_CLAIM.search(text)
    if budget is not None and budget >= 100_000 and small_claim:
        hits.append(RuleHit("conflict_budget_vs_size", "escalate",
                            f"stated budget {budget:.0f} USD/month contradicts '{small_claim.group(0)}'",
                            heuristic=True))
    domain = email_domain(lead.get("email"))
    if (domain in FREE_MAIL_DOMAINS and not lead.get("company") and budget is not None
            and budget >= HIGH_VALUE_BUDGET):
        hits.append(RuleHit("unverifiable_high_value", "escalate",
                            f"{budget:.0f} USD/month claimed from a free webmail address "
                            "with no company named",
                            heuristic=True))
    return hits


def _is_junk(value: str) -> bool:
    v = value.strip().lower()
    if not v:
        return False
    if v in TEST_WORDS:
        return True
    compact = re.sub(r"[^a-z0-9]", "", v)
    return bool(KEYBOARD_MASH.search(compact) or (len(compact) >= 6 and REPEATED_CHUNK.match(compact)))


def check_test_data(lead: Lead) -> list[RuleHit]:
    signals = []
    if _is_junk(lead.get("name")):
        signals.append("name")
    email = lead.get("email").lower()
    local, _, domain = email.partition("@")
    if _is_junk(local) or _is_junk(domain.split(".")[0]):
        signals.append("email")
    if _is_junk(lead.get("company")):
        signals.append("company")
    if _is_junk(lead.clean_message) or (lead.clean_message and " " not in lead.clean_message
                                        and _is_junk(lead.clean_message[:12])):
        signals.append("message")
    if lead.budget_value in SENTINEL_BUDGETS:
        signals.append("budget")
    if len(signals) >= 2:
        return [RuleHit("test_submission", "reject",
                        f"test or junk values in {', '.join(signals)}", heuristic=True)]
    if signals:
        return [RuleHit("possible_test_data", "flag",
                        f"test-like value in {signals[0]}", heuristic=True)]
    return []


def check_manipulation(lead: Lead) -> list[RuleHit]:
    findings = detector.scan_row(lead.raw)
    if not findings:
        return []
    kinds = sorted({f.kind for f in findings})
    quote = "\n".join(f"[{f.field} / {f.kind}] {f.excerpt}" for f in findings)
    return [RuleHit("manipulation_attempt", "escalate",
                    f"lead text contains instruction-like content ({', '.join(kinds)})",
                    quote=quote)]


ROW_RULES = [
    check_required, check_email, check_budget, check_website, check_markup,
    check_sensitive, check_conflicts, check_test_data, check_manipulation,
]


def check_row(lead: Lead, now: datetime) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for rule in ROW_RULES:
        hits.extend(rule(lead))
    hits.extend(check_timestamp(lead, now))
    return hits


# ---------------------------------------------------------------- cross-row rules


def names_match(a: str, b: str) -> bool:
    """Fuzzy person-name match: 'D. Reyes' matches 'Dana Reyes'."""
    na, nb = normalise_name(a).split(), normalise_name(b).split()
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na[-1] == nb[-1]:
        fa, fb = na[0], nb[0]
        if len(na) == 1 or len(nb) == 1 or fa == fb or (
            (len(fa) == 1 and fb.startswith(fa)) or (len(fb) == 1 and fa.startswith(fb))
        ):
            return True
    return difflib.SequenceMatcher(None, " ".join(na), " ".join(nb)).ratio() >= 0.85


def check_cross_rows(leads: list[Lead]) -> dict[int, list[RuleHit]]:
    """Duplicate ids, repeat senders, and duplicates under a different address.

    Keyed by row_number. Only the later row of a pair gets the hit, so the first
    submission is still triaged normally.
    """
    out: dict[int, list[RuleHit]] = {lead.row_number: [] for lead in leads}
    seen_ids: dict[str, Lead] = {}
    for idx, lead in enumerate(leads):
        lid = lead.get("lead_id")
        if lid and lid in seen_ids:
            out[lead.row_number].append(RuleHit(
                "duplicate_lead_id", "escalate",
                f"lead_id {lid} already used on data row {seen_ids[lid].row_number}"))
        elif lid:
            seen_ids[lid] = lead
        email = normalise_email(lead.get("email"))
        company = normalise_company(lead.get("company"))
        for earlier in leads[:idx]:
            same_email = bool(email) and email == normalise_email(earlier.get("email"))
            earlier_company = normalise_company(earlier.get("company"))
            same_company = company == earlier_company
            same_person = names_match(lead.get("name"), earlier.get("name"))
            if not same_email and not (company and same_company and same_person):
                continue
            ref = earlier.lead_id
            differences = []
            if not same_company:
                before = earlier.get("company") or "(blank)"
                after = lead.get("company") or "(blank)"
                differences.append(f"company '{before}' vs '{after}'")
            if (earlier.budget_value is not None and lead.budget_value is not None
                    and earlier.budget_value != lead.budget_value):
                differences.append(
                    f"budget {earlier.budget_value:.0f} vs {lead.budget_value:.0f} USD/month")
            if not same_person:
                differences.append(f"name '{earlier.get('name')}' vs '{lead.get('name')}'")
            if differences:
                out[lead.row_number].append(RuleHit(
                    "conflicting_repeat_submission", "escalate",
                    f"same sender as {ref} but {'; '.join(differences)}", heuristic=True))
            else:
                how = "same email" if same_email else "same company and matching name"
                out[lead.row_number].append(RuleHit(
                    "duplicate_of", "reject",
                    f"duplicate of {ref} ({how}); merge its note into {ref}", heuristic=True))
            break  # compare with the earliest matching row only
    return out


# ------------------------------------------------------------------------ routing

def route(hits: list[RuleHit]) -> tuple[str | None, list[RuleHit]]:
    """Decide whether code closes the row. Returns (decision or None, deciding hits).

    None means the row goes to the model.
    """
    escalations = [h for h in hits if h.action == "escalate"]
    rejections = [h for h in hits if h.action == "reject"]
    manip_or_sensitive = [h for h in escalations
                          if h.code == "manipulation_attempt" or h.code.startswith("sensitive_")]
    if manip_or_sensitive:
        return "ESCALATE", manip_or_sensitive
    junk = [h for h in rejections if h.code == "test_submission"]
    if junk:
        return "REJECT", junk
    if escalations:
        return "ESCALATE", escalations
    if rejections:
        return "REJECT", rejections
    return None, []
