"""Command line entry point: `uv run triage`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import llm, pipeline
from .schema import SchemaError


def load_dotenv(path: Path) -> None:
    """Read KEY=value lines into the environment without overriding it.
    Values are never printed."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Triage inbound leads from a CSV fixture.")
    parser.add_argument("--fixture", type=Path, default=Path("fixtures/inbound_leads.csv"))
    parser.add_argument("--brief", type=Path, default=Path("fixtures/brief.md"),
                        help="challenge brief; its version stamp goes into the run log header")
    parser.add_argument("--backend", choices=["gemini", "offline"], default="gemini")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default runs/<UTC timestamp>-<backend>)")
    args = parser.parse_args(argv)

    load_dotenv(Path(".env"))
    try:
        backend: llm.Backend = (llm.GeminiBackend(model=args.model) if args.backend == "gemini"
                                else llm.OfflineJudge())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    out = args.out or Path("runs") / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{backend.name}"
    try:
        traces = pipeline.run(args.fixture, args.brief, backend, out, now)
    except SchemaError as exc:
        print(f"error: {args.fixture}: {exc}", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    for t in traces:
        assert t.decision
        counts[t.decision.decision] = counts.get(t.decision.decision, 0) + 1
    print(f"{len(traces)} rows -> {out}/decisions.csv, decisions.json, run.log")
    print(", ".join(f"{k}={counts.get(k, 0)}" for k in llm.DECISIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
