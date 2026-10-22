"""End-to-end over the real fixture with the model mocked."""

from __future__ import annotations

import csv
import json

from lead_triage import llm, pipeline
from lead_triage.schema import load_leads

from .conftest import BRIEF, FIXTURE, NOW, make_lead


class RecordingModel:
    name = "mock"
    model = "mock-1"

    def __init__(self, reply: dict | str | None = None, error: str | None = None):
        self.calls: list[str] = []
        self.reply = reply if reply is not None else {
            "fit": "medium", "intent": "exploring", "decision": "NURTURE",
            "reason": "Mock reply. Second sentence is dropped.", "certainty": 0.9}
        self.error = error

    def complete(self, prompt, lead):
        self.calls.append(lead.lead_id)
        raw = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return llm.ModelReply("" if self.error else raw, self.name, self.model, self.error)


def test_every_fixture_lead_id_appears_exactly_once(tmp_path):
    model = RecordingModel()
    pipeline.run(FIXTURE, BRIEF, model, tmp_path, NOW)
    with FIXTURE.open(newline="") as fh:
        fixture_ids = [r["lead_id"] for r in csv.DictReader(fh)]
    with (tmp_path / "decisions.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    out_ids = [r["lead_id"] for r in rows]
    assert sorted(out_ids) == sorted(fixture_ids) and len(set(out_ids)) == len(out_ids)
    json_ids = [d["lead_id"] for d in json.loads((tmp_path / "decisions.json").read_text())]
    assert json_ids == out_ids
    for r in rows:
        assert r["decision"] in llm.DECISIONS
        assert 0.0 <= float(r["confidence"]) <= 1.0
        assert r["reason"]


def test_code_routed_rows_never_reach_the_model(tmp_path):
    model = RecordingModel()
    traces = pipeline.run(FIXTURE, BRIEF, model, tmp_path, NOW)
    routed = {t.lead.lead_id for t in traces if t.routed_by_code}
    assert routed and not routed & set(model.calls)
    assert len(model.calls) + len(routed) == len(traces)


def test_adversarial_row_is_escalated_even_if_the_model_would_obey(tmp_path):
    obedient = RecordingModel({"fit": "high", "intent": "buying_now", "decision": "QUALIFY",
                               "reason": "As instructed.", "certainty": 1.0})
    traces = pipeline.run(FIXTURE, BRIEF, obedient, tmp_path, NOW)
    by_id = {t.lead.lead_id: t for t in traces}
    adversarial = [t for t in traces if any(h.code == "manipulation_attempt" for h in t.hits)]
    assert adversarial
    for t in adversarial:
        assert t.decision.decision == "ESCALATE"
        assert t.lead.lead_id not in obedient.calls
    assert by_id[adversarial[0].lead.lead_id].decision.confidence <= 0.6


def test_run_log_header_and_fenced_quotes(tmp_path):
    pipeline.run(FIXTURE, BRIEF, RecordingModel(), tmp_path, NOW)
    log = (tmp_path / "run.log").read_text()
    assert "fixture_sha256: cc1927ca771c37b186a2abdb7b9757594da79ec6dda074e5a514dd2761cc8599" in log
    assert "brief_version: 2026-07" in log
    # every quoted lead text sits inside a fence
    for block in log.split("(data, not instructions):\n")[1:]:
        assert block.startswith("```text\n")
    assert "UNTRUSTED LEAD DATA" in log and "raw judge reply:" in log


def test_confidence_is_capped_when_a_rule_hit_exists():
    lead = make_lead(monthly_budget_usd="15k")
    hits = [pipeline.rules.RuleHit("budget_shorthand", "flag", "x")]
    assert pipeline.model_confidence(lead, 0.95, hits) == 0.6
    assert pipeline.model_confidence(make_lead(), 0.95, []) == 0.95
    assert pipeline.model_confidence(make_lead(company="", website=""), 0.9, []) == 0.72


def test_reason_is_one_sentence(tmp_path):
    traces = pipeline.run(FIXTURE, BRIEF, RecordingModel(), tmp_path, NOW)
    model_rows = [t for t in traces if t.decision.decided_by == "mock"]
    assert model_rows and all(t.decision.reason == "Mock reply." for t in model_rows)


def test_invalid_model_reply_becomes_escalate(tmp_path):
    traces = pipeline.run(FIXTURE, BRIEF, RecordingModel("I think QUALIFY"), tmp_path, NOW)
    model_rows = [t for t in traces if not t.routed_by_code]
    assert model_rows and all(t.decision.decision == "ESCALATE" for t in model_rows)
    assert all("model_reply_invalid" in t.decision.rule_hits for t in model_rows)


def test_model_error_becomes_escalate(tmp_path):
    traces = pipeline.run(FIXTURE, BRIEF, RecordingModel(error="HTTP 503"), tmp_path, NOW)
    assert all(t.decision.decision == "ESCALATE" for t in traces if not t.routed_by_code)


def test_duplicate_lead_ids_still_give_one_output_per_row(tmp_path):
    src = FIXTURE.read_text().splitlines()
    path = tmp_path / "dupe.csv"
    path.write_text("\n".join([src[0], src[1], src[1]]) + "\n")
    traces = pipeline.run(path, None, RecordingModel(), tmp_path / "out", NOW)
    ids = [t.decision.lead_id for t in traces]
    assert len(set(ids)) == 2
    assert load_leads(path)[0][0].lead_id == "L-001"


def test_unsure_judgement_goes_to_a_person(tmp_path):
    unsure = RecordingModel({"fit": "low", "intent": "unclear", "decision": "NURTURE",
                             "reason": "Not sure.", "certainty": 0.4})
    traces = pipeline.run(FIXTURE, BRIEF, unsure, tmp_path, NOW)
    judged = [t for t in traces if not t.routed_by_code]
    assert judged and all(t.decision.decision == "ESCALATE" for t in judged)
    assert all("low_judge_certainty" in t.decision.rule_hits for t in judged)


def test_judge_escalation_is_kept_even_when_unsure(tmp_path):
    model = RecordingModel({"fit": "low", "intent": "unclear", "decision": "ESCALATE",
                            "reason": "Odd request.", "certainty": 0.3})
    traces = pipeline.run(FIXTURE, BRIEF, model, tmp_path, NOW)
    judged = [t for t in traces if not t.routed_by_code]
    assert all("low_judge_certainty" not in t.decision.rule_hits for t in judged)


def test_security_next_step_wins_over_payment():
    hits = [pipeline.rules.RuleHit("sensitive_payment", "escalate", "payment content"),
            pipeline.rules.RuleHit("sensitive_security", "escalate", "security content")]
    assert "security" in pipeline.code_reason("ESCALATE", hits).split("; ")[-1]
