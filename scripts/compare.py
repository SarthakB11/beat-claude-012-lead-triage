"""Compare the agent's decisions with a manual pass.

    uv run python scripts/compare.py                        # newest run in runs/
    uv run python scripts/compare.py --decisions runs/<id>/decisions.csv

manual_labels.csv has the columns lead_id, decision, note. Fill it by hand
before reading any decisions file, or the comparison measures nothing.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

DECISIONS = ["QUALIFY", "NURTURE", "REJECT", "ESCALATE"]


def read_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["lead_id"].strip(): r for r in csv.DictReader(fh) if (r.get("lead_id") or "").strip()}


def newest_decisions(runs: Path) -> Path:
    candidates = sorted(runs.glob("*/decisions.csv"))
    if not candidates:
        raise SystemExit(f"no decisions.csv under {runs}/")
    return candidates[-1]


def compare(manual: dict[str, dict[str, str]], agent: dict[str, dict[str, str]]) -> dict[str, object]:
    labelled = {k: v for k, v in manual.items() if (v.get("decision") or "").strip()}
    bad_labels = sorted(k for k, v in labelled.items() if v["decision"].strip().upper() not in DECISIONS)
    pairs = []
    for lead_id, row in sorted(labelled.items()):
        if lead_id in bad_labels or lead_id not in agent:
            continue
        pairs.append((lead_id, row["decision"].strip().upper(), agent[lead_id]["decision"].strip().upper()))
    agree = [p for p in pairs if p[1] == p[2]]
    disagree = [p for p in pairs if p[1] != p[2]]
    kinds = Counter()
    for _, human, bot in disagree:
        if bot == "ESCALATE":
            kinds["agent escalated a row the manual pass decided"] += 1
        elif human == "ESCALATE":
            kinds["agent decided a row the manual pass escalated"] += 1
        elif bot == "QUALIFY":
            kinds["agent qualified a row the manual pass did not"] += 1
        elif human == "QUALIFY":
            kinds["agent missed a row the manual pass qualified"] += 1
        else:
            kinds["other decision swap"] += 1
    return {
        "pairs": pairs,
        "agree": agree,
        "disagree": disagree,
        "kinds": kinds,
        "bad_labels": bad_labels,
        "unlabelled": sorted(set(agent) - set(labelled)),
        "not_in_agent": sorted(set(labelled) - set(agent)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manual", type=Path, default=Path("manual_labels.csv"))
    parser.add_argument("--decisions", type=Path, default=None)
    args = parser.parse_args(argv)
    decisions_path = args.decisions or newest_decisions(Path("runs"))
    manual = read_rows(args.manual)
    agent = read_rows(decisions_path)
    result = compare(manual, agent)
    pairs, agree, disagree = result["pairs"], result["agree"], result["disagree"]
    print(f"manual labels: {args.manual}")
    print(f"agent decisions: {decisions_path}")
    if not pairs:
        print("no labelled rows to compare yet; fill manual_labels.csv first")
        return 2
    print(f"compared rows: {len(pairs)} of {len(agent)} agent rows")
    print(f"agreement: {len(agree)}/{len(pairs)} = {100 * len(agree) / len(pairs):.0f}%")
    if result["unlabelled"]:
        print(f"not labelled by hand: {', '.join(result['unlabelled'])}")
    if result["bad_labels"]:
        print(f"labels that are not a valid decision (ignored): {', '.join(result['bad_labels'])}")
    if result["not_in_agent"]:
        print(f"labelled ids missing from the agent file: {', '.join(result['not_in_agent'])}")
    print()
    print("confusion (rows = manual, columns = agent):")
    print("          " + " ".join(f"{d[:8]:>9}" for d in DECISIONS))
    for human in DECISIONS:
        cells = [sum(1 for _, h, b in pairs if h == human and b == bot) for bot in DECISIONS]
        print(f"{human[:9]:<10}" + " ".join(f"{c:>9}" for c in cells))
    print()
    if disagree:
        print("disagreements:")
        for lead_id, human, bot in disagree:
            note = manual[lead_id].get("note", "").strip()
            print(f"  {lead_id}: manual {human}, agent {bot}")
            print(f"    agent reason: {agent[lead_id].get('reason', '')}")
            if note:
                print(f"    manual note:  {note}")
        print()
        print("analysis:")
        for kind, count in result["kinds"].most_common():
            print(f"  {count} x {kind}")
        if result["kinds"].get("agent qualified a row the manual pass did not"):
            print("  the costliest kind is present: a QUALIFY on a row a person "
                  "would not send to sales")
    else:
        print("no disagreements")
    return 0


if __name__ == "__main__":
    sys.exit(main())
