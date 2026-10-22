from __future__ import annotations

import pytest

from lead_triage.schema import (
    SchemaError,
    clean_text,
    load_leads,
    normalise_company,
    normalise_email,
    parse_budget,
    read_brief_version,
    sha256_of,
)

from .conftest import BRIEF, FIXTURE


def test_fixture_checksum_matches_the_copy_we_document():
    assert sha256_of(FIXTURE) == "cc1927ca771c37b186a2abdb7b9757594da79ec6dda074e5a514dd2761cc8599"


def test_brief_version_is_read_from_the_brief():
    assert read_brief_version(BRIEF) == "2026-07"


def test_brief_version_unknown_when_file_missing(tmp_path):
    assert read_brief_version(tmp_path / "nope.md").startswith("unknown")


def test_missing_columns_raise(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("lead_id,name\nX-1,Someone\n")
    with pytest.raises(SchemaError, match="missing columns"):
        load_leads(bad)


def test_extra_columns_warn(tmp_path):
    src = FIXTURE.read_text().splitlines()
    rows = [src[0] + ",utm"] + [line + ",x" for line in src[1:3]]
    path = tmp_path / "extra.csv"
    path.write_text("\n".join(rows) + "\n")
    leads, warnings = load_leads(path)
    assert len(leads) == 2
    assert any("extra columns" in w for w in warnings)


@pytest.mark.parametrize(
    ("raw", "value", "note_contains"),
    [
        ("25000", 25000.0, None),
        ("15k", 15000.0, "shorthand"),
        ("$12,500", 12500.0, None),
        ("we'll discuss", None, "not a number"),
        ("", None, "empty"),
        ("-500", -500.0, None),
    ],
)
def test_parse_budget(raw, value, note_contains):
    got_value, note = parse_budget(raw)
    assert got_value == value
    if note_contains is None:
        assert note is None
    else:
        assert note_contains in note


def test_clean_text_strips_tags_entities_and_zero_width():
    assert clean_text("<div>Hi &amp; bye​</div><!-- hidden -->") == "Hi & bye"


def test_normalisers():
    assert normalise_company("Bright Cart Inc") == normalise_company("BrightCart")
    assert normalise_email("Dana.Reyes+ads@Gmail.com") == "danareyes@gmail.com"
