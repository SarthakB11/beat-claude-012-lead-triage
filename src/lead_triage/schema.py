"""Loading the lead CSV and normalising its fields.

Nothing here makes a routing decision. It only turns raw strings into values the
rule layer can compare, and records what it had to do to get there.
"""

from __future__ import annotations

import csv
import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

EXPECTED_COLUMNS = [
    "lead_id",
    "submitted_at",
    "name",
    "email",
    "company",
    "website",
    "monthly_budget_usd",
    "message",
    "source",
]

# Fields a lead cannot be triaged without. Company and website are optional: a
# student or a solo founder may legitimately leave them blank.
REQUIRED_FIELDS = ["lead_id", "submitted_at", "name", "email", "message"]

COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "co", "corp", "corporation",
    "gmbh", "sa", "sas", "bv", "plc", "pty", "group", "the",
}


class SchemaError(ValueError):
    """The file cannot be processed at all (wrong or missing columns)."""


@dataclass
class Lead:
    row_number: int  # 1-based data row number in the file, header excluded
    raw: dict[str, str]
    budget_value: float | None = None
    budget_note: str | None = None  # how the budget string was interpreted
    clean_message: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def lead_id(self) -> str:
        return self.raw.get("lead_id", "").strip() or f"ROW-{self.row_number:03d}"

    def get(self, key: str) -> str:
        return (self.raw.get(key) or "").strip()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_brief_version(brief_path: Path | None) -> str:
    if brief_path is None or not brief_path.exists():
        return "unknown (brief file not found)"
    text = brief_path.read_text(encoding="utf-8")
    match = re.search(r"Brief version:\**\s*([0-9]{4}-[0-9]{2})", text)
    return match.group(1) if match else "unknown (no version stamp in brief)"


def load_leads(path: Path) -> tuple[list[Lead], list[str]]:
    """Read the CSV. Raises SchemaError when required columns are missing.

    Returns the leads and a list of schema warnings (extra columns, short rows).
    """
    warnings: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in EXPECTED_COLUMNS if c not in header]
        if missing:
            raise SchemaError(f"missing columns: {', '.join(missing)}")
        extra = [c for c in header if c not in EXPECTED_COLUMNS]
        if extra:
            warnings.append(f"extra columns ignored: {', '.join(extra)}")
        leads: list[Lead] = []
        for i, row in enumerate(reader, start=1):
            clean = {(k or "").strip(): (v if isinstance(v, str) else "") for k, v in row.items()}
            if None in row:  # more cells than headers
                warnings.append(f"row {i}: more cells than columns; extra cells ignored")
            lead = Lead(row_number=i, raw={c: clean.get(c, "") or "" for c in EXPECTED_COLUMNS})
            lead.budget_value, lead.budget_note = parse_budget(lead.get("monthly_budget_usd"))
            lead.clean_message = clean_text(lead.get("message"))
            leads.append(lead)
    return leads, warnings


def parse_budget(value: str) -> tuple[float | None, str | None]:
    """Parse a monthly budget string.

    Returns (amount, note). note is None for a plain number, otherwise it says
    how the value was read ("shorthand", "not a number", "empty").
    """
    text = value.strip().replace(",", "").replace("$", "").replace("USD", "").strip()
    if not text:
        return None, "empty"
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return float(text), None
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*([kKmM])", text)
    if match:
        mult = 1_000 if match.group(2).lower() == "k" else 1_000_000
        amount = float(match.group(1)) * mult
        return amount, f"shorthand '{value.strip()}' read as {amount:.0f}"
    return None, f"not a number: '{value.strip()}'"


ZERO_WIDTH = re.compile("[​-‏‪-‮⁠-⁤﻿]")
TAG = re.compile(r"<[^>]{0,200}>")


def clean_text(value: str) -> str:
    """Text as a human would read it: entities decoded, tags and zero-width
    characters removed, whitespace collapsed. The detector runs on the raw text,
    never on this."""
    text = html.unescape(value)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = TAG.sub(" ", text)
    text = ZERO_WIDTH.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split())


def normalise_email(value: str) -> str:
    email = value.strip().lower()
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def email_domain(value: str) -> str:
    return value.strip().lower().rpartition("@")[2] if "@" in value else ""


def normalise_company(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", text)
    words = [w for w in words if w not in COMPANY_SUFFIXES]
    return "".join(words)


def website_domain(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/", 1)[0].split(":", 1)[0]
    return text.removeprefix("www.")


def normalise_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z]+", text))
