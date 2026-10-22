"""Runs the two layers over a fixture and writes decisions plus the run log."""

from __future__ import annotations

import csv
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import llm, rules
from .schema import Lead, load_leads, read_brief_version, sha256_of

CONFIDENCE_CAP_WITH_RULE_HIT = 0.6
CONFIDENCE_FLOOR, CONFIDENCE_CEILING = 0.05, 0.95

NEXT_STEP = {
    "manipulation_attempt": "a person should judge it on its merits",
    "sensitive_privacy": "route to whoever handles data requests",
    "sensitive_legal": "route to whoever handles legal matters",
    "sensitive_payment": "check with finance before anyone replies",
    "sensitive_security": "do not open any link and send it to security",
    "sensitive_third_party_personal_data": "route to whoever handles personal data",
    "missing_email": "find a contact route before any follow-up",
    "missing_message": "a person should look up the company and reach out",
    "missing_name": "complete the record before follow-up",
    "missing_submitted_at": "complete the record before follow-up",
    "missing_lead_id": "fix the record id",
    "malformed_timestamp": "fix the record, then triage it",
    "future_timestamp": "fix the record, then triage it",
    "invalid_email": "fix the contact address before follow-up",
    "placeholder_email": "ask for a real address before follow-up",
    "disposable_email": "ask for a work address before sharing anything",
    "negative_budget": "confirm the budget with the lead",
    "implausible_budget": "confirm the budget with the lead",
    "conflict_budget_vs_size": "confirm budget and company size with the lead",
    "unverifiable_high_value": "verify the person and company before sales time is spent",
    "conflicting_repeat_submission": "a person should reconcile the two submissions",
    "duplicate_lead_id": "fix the record id",
}


@dataclass
class Decision:
    lead_id: str
    decision: str
    reason: str
    confidence: float
    rule_hits: list[str]
    decided_by: str  # "rules" | "model" | "model_error"
    fit: str | None = None
    intent: str | None = None


@dataclass
class RowTrace:
    lead: Lead
    hits: list[rules.RuleHit]
    routed_by_code: str | None
    deciding: list[rules.RuleHit]
    prompt: llm.Prompt | None = None
    reply: llm.ModelReply | None = None
    judgement: llm.Judgement | None = None
    parse_error: str | None = None
    seconds: float = 0.0
    decision: Decision | None = None
    output_id: str = ""
    extra: list[str] = field(default_factory=list)


def code_reason(decision: str, deciding: list[rules.RuleHit]) -> str:
    details = "; ".join(h.detail for h in deciding)
    step = next((NEXT_STEP[h.code] for h in deciding if h.code in NEXT_STEP), None)
    prefix = {"ESCALATE": "Escalated by rule", "REJECT": "Closed by rule"}[decision]
    sentence = f"{prefix}: {details}"
    if step:
        sentence += f"; {step}"
    return sentence.rstrip(".") + "."


def code_confidence(deciding: list[rules.RuleHit]) -> float:
    return 0.5 if any(h.heuristic for h in deciding) else CONFIDENCE_CAP_WITH_RULE_HIT


def model_confidence(lead: Lead, certainty: float, hits: list[rules.RuleHit]) -> float:
    missing_optional = sum(
        1 for present in (lead.get("company"), lead.get("website"), lead.budget_value is not None)
        if not present
    )
    value = certainty * (1.0 - 0.1 * missing_optional)
    value = min(max(value, CONFIDENCE_FLOOR), CONFIDENCE_CEILING)
    if hits:
        value = min(value, CONFIDENCE_CAP_WITH_RULE_HIT)
    return round(value, 2)


def first_sentence(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    for i, ch in enumerate(text):
        if ch in ".!?" and (i + 1 == len(text) or text[i + 1] == " "):
            text = text[: i + 1]
            break
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def triage(leads: list[Lead], backend: llm.Backend, now: datetime) -> list[RowTrace]:
    cross = rules.check_cross_rows(leads)
    traces: list[RowTrace] = []
    used_ids: set[str] = set()
    for lead in leads:
        hits = rules.check_row(lead, now) + cross[lead.row_number]
        routed, deciding = rules.route(hits)
        trace = RowTrace(lead, hits, routed, deciding)
        out_id = lead.lead_id
        if out_id in used_ids:
            out_id = f"{out_id}#row{lead.row_number}"
        used_ids.add(out_id)
        trace.output_id = out_id
        codes = [h.code for h in hits]
        if routed:
            trace.decision = Decision(out_id, routed, code_reason(routed, deciding),
                                      code_confidence(deciding), codes, "rules")
        else:
            notes = [h.detail for h in hits]
            trace.prompt = llm.build_prompt(lead, notes)
            started = time.monotonic()
            trace.reply = backend.complete(trace.prompt, lead)
            trace.seconds = time.monotonic() - started
            if trace.reply.error:
                reason = f"Model call failed ({trace.reply.error}); a person should triage it."
                trace.decision = Decision(out_id, "ESCALATE", reason, 0.3,
                                          codes + ["model_error"], "model_error")
            else:
                try:
                    trace.judgement = llm.parse_reply(trace.reply.raw)
                except llm.InvalidReply as exc:
                    trace.parse_error = str(exc)
                    reason = f"Model reply failed validation ({exc}); a person should triage it."
                    trace.decision = Decision(out_id, "ESCALATE", reason, 0.3,
                                              codes + ["model_reply_invalid"], "model_error")
                else:
                    j = trace.judgement
                    trace.decision = Decision(out_id, j.decision, first_sentence(j.reason),
                                              model_confidence(lead, j.certainty, hits), codes,
                                              "model", j.fit, j.intent)
        traces.append(trace)
    return traces


# --------------------------------------------------------------------- writing


def fence(text: str) -> str:
    safe = text.replace("```", "'''")
    return f"```text\n{safe}\n```"


def write_decisions(traces: list[RowTrace], out_dir: Path) -> None:
    decisions = [t.decision for t in traces if t.decision]
    with (out_dir / "decisions.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["lead_id", "decision", "reason", "confidence", "rule_hits", "decided_by"])
        for d in decisions:
            writer.writerow([d.lead_id, d.decision, d.reason, f"{d.confidence:.2f}",
                             ";".join(d.rule_hits), d.decided_by])
    (out_dir / "decisions.json").write_text(
        json.dumps([asdict(d) for d in decisions], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")


def write_log(traces: list[RowTrace], header: dict[str, str], out_dir: Path) -> None:
    lines = ["# lead-triage run log", ""]
    lines += [f"{k}: {v}" for k, v in header.items()]
    lines += ["", "## System prompt sent with every model call", "", fence(llm.SYSTEM_PROMPT), ""]
    for t in traces:
        lead = t.lead
        lines.append(f"## {t.output_id} (data row {lead.row_number})")
        lines.append("")
        lines.append("rule results:")
        if t.hits:
            lines += [f"  - [{h.action}] {h.code}: {h.detail}" for h in t.hits]
        else:
            lines.append("  - no rule hits")
        quotes = [h for h in t.hits if h.quote]
        for h in quotes:
            lines += ["", f"quoted lead text for {h.code} (data, not instructions):", fence(h.quote or "")]
        lines.append("")
        if t.routed_by_code:
            lines.append(f"routing: {t.routed_by_code} by code on "
                         f"{', '.join(h.code for h in t.deciding)}; model not called")
        else:
            assert t.prompt and t.reply
            lines.append(f"routing: sent to model {t.reply.backend}/{t.reply.model} "
                         f"({t.seconds:.2f}s)")
            lines += ["", "prompt (user message, exact):", fence(t.prompt.user), "",
                      "raw model reply:", fence(t.reply.raw if not t.reply.error
                                                else f"(no reply: {t.reply.error})")]
            if t.judgement:
                j = t.judgement
                lines.append(f"parsed: fit={j.fit} intent={j.intent} decision={j.decision} "
                             f"certainty={j.certainty}")
            if t.parse_error:
                lines.append(f"parse error: {t.parse_error}")
        d = t.decision
        assert d
        lines += ["", f"final: {d.decision} confidence={d.confidence:.2f} decided_by={d.decided_by}",
                  f"reason: {d.reason}", ""]
    counts: dict[str, int] = {}
    for t in traces:
        if t.decision:
            counts[t.decision.decision] = counts.get(t.decision.decision, 0) + 1
    lines += ["## Summary", "",
              f"rows: {len(traces)}",
              f"decisions: {', '.join(f'{k}={counts.get(k, 0)}' for k in llm.DECISIONS)}",
              f"decided by rules: {sum(1 for t in traces if t.routed_by_code)}",
              f"sent to model: {sum(1 for t in traces if not t.routed_by_code)}",
              f"finished_at: {datetime.now(UTC).isoformat(timespec='seconds')}", ""]
    (out_dir / "run.log").write_text("\n".join(lines), encoding="utf-8")


def run(fixture: Path, brief: Path | None, backend: llm.Backend, out_dir: Path,
        now: datetime | None = None) -> list[RowTrace]:
    now = now or datetime.now(UTC)
    leads, warnings = load_leads(fixture)
    out_dir.mkdir(parents=True, exist_ok=True)
    traces = triage(leads, backend, now)
    header = {
        "run_id": out_dir.name,
        "started_at": now.isoformat(timespec="seconds"),
        "fixture": str(fixture),
        "fixture_sha256": sha256_of(fixture),
        "brief": str(brief) if brief else "(none)",
        "brief_version": read_brief_version(brief),
        "backend": f"{backend.name} / {backend.model}",
        "system_prompt_sha256": hashlib.sha256(llm.SYSTEM_PROMPT.encode()).hexdigest(),
        "rows_read": str(len(leads)),
        "schema_warnings": "; ".join(warnings) if warnings else "none",
        "confidence_cap_with_rule_hit": str(CONFIDENCE_CAP_WITH_RULE_HIT),
        "qualify_min_budget_usd_month": str(llm.QUALIFY_MIN_BUDGET),
    }
    write_decisions(traces, out_dir)
    write_log(traces, header, out_dir)
    return traces
