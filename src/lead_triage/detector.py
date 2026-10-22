"""Manipulation detector.

Looks for text in a lead that tries to steer an automated reader: instruction
overrides, role play, decision directives, chat-format role tags, zero-width
characters and HTML comments. It runs on the raw cell values, before any
cleaning, because cleaning would remove exactly the hidden parts it looks for.

A hit never changes how the row is classified on its merits; it routes the row
to a human and the matched text is quoted in the run log inside a fenced block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCANNED_FIELDS = ["name", "email", "company", "website", "monthly_budget_usd", "message", "source"]

ZERO_WIDTH = re.compile("[​-‏‪-‮⁠-⁤﻿]")
HTML_COMMENT = re.compile(r"<!--.*?(-->|$)", re.S)
SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\b[^.!?\n]{0,40}"
            r"\b(instructions?|prompts?|rules?|guidelines|previous|prior|above|everything)\b",
            re.I,
        ),
    ),
    (
        "role_play",
        re.compile(r"\b(you are now|you are an?|act as|pretend (to be|you are)|from now on you)\b", re.I),
    ),
    (
        "decision_directive",
        re.compile(
            r"\b(classify|mark|label|tag|route|score|rate|set|treat)\b[^.!?\n]{0,40}"
            r"\b(as|to|with)\b[^.!?\n]{0,20}\b(qualif\w*|nurture|reject\w*|escalat\w*|hot|priority|"
            r"high|confidence)\b",
            re.I,
        ),
    ),
    ("decision_directive", re.compile(r"\b(qualify|approve|accept)\s+(this|me|us)\b", re.I)),
    ("decision_directive", re.compile(r"\bdo not (escalate|reject|flag)\b", re.I)),
    (
        "confidence_directive",
        re.compile(r"\bconfidence\b[^.!?\n]{0,15}(1(\.0+)?|0?\.\d+|100\s?%)", re.I),
    ),
    # The output vocabulary written in capitals inside lead text is a strong tell.
    ("decision_vocabulary", re.compile(r"\b(QUALIFY|NURTURE|REJECT|ESCALATE)\b")),
    ("role_tag", re.compile(r"(?im)^\s*(system|assistant|developer|user)\s*:")),
    ("role_tag", re.compile(r"(?i)<\|?/?\s*(system|assistant|developer|im_start|im_end|user)\s*\|?>")),
    ("role_tag", re.compile(r"\[/?INST\]|<<SYS>>|###\s*(system|instruction)", re.I)),
    ("prompt_reference", re.compile(r"\b(system prompt|previous prompt|new instructions?)\b", re.I)),
]


@dataclass(frozen=True)
class Finding:
    field: str
    kind: str
    matched: str
    excerpt: str  # the sentence around the match, raw, with invisible chars made visible


def make_visible(text: str) -> str:
    """Render invisible characters as <U+XXXX> so a log reader can see them."""
    return ZERO_WIDTH.sub(lambda m: f"<U+{ord(m.group(0)):04X}>", text)


def _sentence_around(text: str, start: int, end: int, limit: int = 240) -> str:
    # A sentence ends at . ! ? followed by whitespace or the end of the text, or at a
    # newline, so "confidence 1.0" is not split at the decimal point.
    boundaries = [m.end() for m in SENTENCE_END.finditer(text)]
    left = max((b for b in boundaries if b <= start), default=0)
    right = min((b for b in boundaries if b >= end), default=len(text))
    excerpt = text[left:right].strip()
    if len(excerpt) > limit:
        excerpt = excerpt[:limit] + "..."
    return make_visible(excerpt)


def scan_text(field: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    if not text:
        return findings
    zw = ZERO_WIDTH.findall(text)
    if zw:
        codes = ", ".join(sorted({f"U+{ord(c):04X}" for c in zw}))
        findings.append(Finding(field, "zero_width_characters", codes, make_visible(text[:240])))
    for match in HTML_COMMENT.finditer(text):
        comment = match.group(0)
        findings.append(Finding(field, "html_comment", comment[:80], make_visible(comment[:240])))
    seen: set[tuple[str, int]] = set()
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            key = (kind, match.start())
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(field, kind, match.group(0), _sentence_around(text, match.start(), match.end()))
            )
    return findings


def scan_row(raw: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    for field in SCANNED_FIELDS:
        findings.extend(scan_text(field, raw.get(field) or ""))
    return findings
