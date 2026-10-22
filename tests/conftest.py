from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lead_triage.schema import EXPECTED_COLUMNS, Lead, clean_text, parse_budget

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inbound_leads.csv"
BRIEF = ROOT / "fixtures" / "brief.md"
NOW = datetime(2026, 10, 22, tzinfo=UTC)

CLEAN = {
    "lead_id": "T-001",
    "submitted_at": "2026-06-01T09:00:00Z",
    "name": "Ana Silva",
    "email": "ana@acmeco.com",
    "company": "Acme Co",
    "website": "https://acmeco.com",
    "monthly_budget_usd": "12000",
    "message": "We need help with paid search and landing pages. Budget approved, start next month.",
    "source": "contact_form",
}


def make_lead(row_number: int = 1, **overrides: str) -> Lead:
    raw = {c: CLEAN[c] for c in EXPECTED_COLUMNS}
    raw.update(overrides)
    lead = Lead(row_number=row_number, raw=raw)
    lead.budget_value, lead.budget_note = parse_budget(raw["monthly_budget_usd"])
    lead.clean_message = clean_text(raw["message"])
    return lead


@pytest.fixture
def lead_factory():
    return make_lead
