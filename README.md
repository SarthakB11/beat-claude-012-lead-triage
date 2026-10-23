# Beat Claude 012: lead triage agent

First-pass triage for inbound marketing leads. It reads a CSV of web-form
leads and gives every row one of four decisions (QUALIFY, NURTURE, REJECT,
ESCALATE), with a one-sentence reason, a confidence between 0 and 1, and the
rules that fired. It was built for the Single Grain "Beat Claude" challenge
AI Automation Intern 012 (brief version 2026-07) and runs on that challenge's
fixture, which is copied into `fixtures/` (see `fixtures/SOURCE.md`).

It works in two layers. Plain code checks every row first: schema, required
fields, malformed values, duplicates, conflicting signals, sensitive content
and text that tries to instruct the agent. Code closes the rows it is sure
about and routes risky ones to a person. Only the remaining rows go to a
judge, which is one Gemini call per lead (or an offline keyword judge when no
API key is available). The write-up is in [SUBMISSION.md](SUBMISSION.md) and
the reasoning behind each choice is in [docs/design.md](docs/design.md).

## Run it

Python 3.12 and [uv](https://docs.astral.sh/uv/) are the only requirements.

```bash
uv sync
uv run triage --backend offline
```

That writes `runs/<UTC timestamp>-offline/` with `decisions.csv`,
`decisions.json` and `run.log`. To use the real model, put the key in the
environment (or in a `.env` file in the repo root, which git ignores) and drop
the flag. Surrounding whitespace in the key, such as a carriage return from a
Windows `.env`, is stripped:

```bash
export GEMINI_API_KEY=...   # never committed, never logged
uv run triage               # backend gemini, model gemini-2.5-flash
uv run triage --model gemini-2.5-pro
```

## Run it on a fresh fixture

```bash
uv run triage --fixture path/to/new_leads.csv --brief path/to/brief.md
```

The CSV needs the columns `lead_id, submitted_at, name, email, company,
website, monthly_budget_usd, message, source`; extra columns are ignored with a
warning and missing ones stop the run. The brief is only read for its version
stamp, which goes into the log header next to the fixture's sha256.

## Where to look

- `runs/<id>/run.log`: the run header (fixture sha256, brief version, backend,
  system prompt), then for every row the rule results, any quoted lead text in
  a fenced block, the exact prompt sent, the raw reply, the parsed fields and
  the final decision.
- `runs/<id>/decisions.csv` and `decisions.json`: one entry per lead_id.
- `runs/20261022T214331Z-gemini/`: the submitted run, Gemini 2.5 Flash after
  the intent fix.
- `runs/20261022T214152Z-gemini/`: the first Gemini run, kept as produced.
- `runs/20261022T171209Z-offline/` and `runs/20261022T171335Z-offline/`: the
  two earlier runs with the offline judge, before and after their fixes.
  SUBMISSION.md describes all four.

## Tests and checks

```bash
uv run pytest -q        # offline, the model call is mocked
uv run ruff check .
```

## Compare with a manual pass

Label every row by hand in `manual_labels.csv` (columns `lead_id, decision,
note`) before reading any decisions file, then:

```bash
uv run python scripts/compare.py                      # newest run
uv run python scripts/compare.py --decisions runs/<id>/decisions.csv
```

It prints agreement, a confusion table, every disagreement with the agent's
reason and your note, and which way the disagreements lean.

## Layout

```text
src/lead_triage/schema.py    CSV loading, normalisers, budget parsing, text cleaning
src/lead_triage/rules.py     the rule layer and routing precedence
src/lead_triage/detector.py  manipulation detector
src/lead_triage/llm.py       prompt, Gemini backend, offline judge, reply validation
src/lead_triage/pipeline.py  orchestration, confidence, decisions files, run log
src/lead_triage/cli.py       `uv run triage`
scripts/compare.py           agent versus manual labels
tests/                       one synthetic row per rule, detector cases, mocked model, fixture coverage
```
