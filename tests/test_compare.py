from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compare", ROOT / "scripts" / "compare.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def write(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


def test_compare_counts_agreement_and_kinds(tmp_path, capsys):
    manual = write(tmp_path / "m.csv", "lead_id,decision,note",
                   ["A,QUALIFY,good", "B,REJECT,", "C,NURTURE,", "D,ESCALATE,odd"])
    agent = write(tmp_path / "a.csv", "lead_id,decision,reason,confidence,rule_hits,decided_by",
                  ["A,QUALIFY,r,0.8,,x", "B,ESCALATE,r,0.6,,x", "C,QUALIFY,r,0.6,,x",
                   "D,NURTURE,r,0.5,,x", "E,REJECT,r,0.5,,x"])
    assert compare.main(["--manual", str(manual), "--decisions", str(agent)]) == 0
    out = capsys.readouterr().out
    assert "agreement: 1/4 = 25%" in out
    assert "not labelled by hand: E" in out
    assert "agent escalated a row the manual pass decided" in out
    assert "agent decided a row the manual pass escalated" in out
    assert "costliest kind is present" in out


def test_compare_with_header_only_labels_exits_2(tmp_path, capsys):
    manual = write(tmp_path / "m.csv", "lead_id,decision,note", [])
    agent = write(tmp_path / "a.csv", "lead_id,decision,reason,confidence,rule_hits,decided_by",
                  ["A,QUALIFY,r,0.8,,x"])
    assert compare.main(["--manual", str(manual), "--decisions", str(agent)]) == 2
    assert "fill manual_labels.csv first" in capsys.readouterr().out


def test_repo_manual_labels_file_has_the_header():
    assert (ROOT / "manual_labels.csv").read_text().splitlines()[0] == "lead_id,decision,note"
