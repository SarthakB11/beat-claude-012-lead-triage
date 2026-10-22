# AI usage

## Tools

- **Claude Code (Claude Opus 5.5)** wrote the code, the tests, the first fixture
  runs and the first draft of these documents, working from the spec prompt
  below.
- **Gemini 2.5 Flash** over the Gemini REST API is the judge the agent is
  built for (`--backend gemini`). It was not called on the fixture: the build
  environment had no Gemini API key, so both committed runs used the offline
  keyword judge (`--backend offline`). The Gemini path is covered by tests with
  the HTTP call mocked, which check the request shape, that the key travels
  only in a header, and that errors become ESCALATE.
- **uv, pytest and ruff** for the environment, tests and lint, and the
  challenge's own `scripts/validate_submission.py` for the packet pre-screen.

## The prompts

The design was fixed in one spec prompt, written before any code. It is quoted
as given:

```text
Clone https://github.com/ericosiu/beat-claude and read challenges/ai-automation-intern-012/brief.md, scoring_rubric.md, SCORING.md and submissions/README.md before writing any code. Then build my submission for "AI Automation Intern 012: The Lead Triage Agent" in a new repo of mine, Python 3.12 with uv, pytest and ruff. Author every commit as me (Sarthak Bhardwaj, sarthakbhardwaj1102@gmail.com), no AI co-author trailers.

Design I want, and the reason for each choice goes in docs/design.md:
1. A two-layer agent: plain code first (schema check, required-field check, duplicate detection by normalised email and company plus fuzzy name, malformed values like impossible dates, negative budgets, placeholder emails, sensitive-request detection by keyword list), then one LLM call per lead ONLY for the judgement fields (fit, intent, decision, reason, confidence), with the lead text passed as data inside a delimited block and a system prompt that says the content is untrusted and may contain instructions to ignore. The LLM never sees a row that plain code already routed to ESCALATE or REJECT; code records why.
2. Hard rules in code, not in the prompt: any row with missing required fields, conflicting signals (for example a budget that contradicts the company size, or two different companies for one email), malformed values, sensitive content (legal threats, personal data of third parties, payment details) or detected manipulation text goes to ESCALATE with the reason quoted in a code block in the log. Confidence is computed by code from the rule hits and the model's self-reported certainty, capped at 0.6 for any row with a rule hit.
3. Manipulation defence: a detector for instruction-like phrasing in free text ("ignore", "you are", "qualify this", "score", "system:", role tags, zero-width characters, HTML comments); a hit flags the row as adversarial, routes it to ESCALATE, and the quoted text goes into the log inside a fenced block only.
4. Outputs: decisions.csv and decisions.json (one entry per lead_id, every row of the fixture, with lead_id, decision, reason, confidence, rule_hits); run.log with a per-row trace (rule results, the exact prompt sent, the raw model reply, parsed fields, final decision) and a run header with the fixture sha256 and the brief version from the brief file.
5. A manual pass: I will hand-label every row myself in manual_labels.csv before I look at the agent output; the repo then has scripts/compare.py that prints agreement, the disagreement rows and a short analysis, so the write-up can carry a Tier 4 before-and-after number labelled [Observed]. Leave manual_labels.csv empty with the header for me to fill.
6. Tests: every rule has a unit test with a synthetic row; the manipulation detector has tests for the obvious phrasings and for zero-width characters; a test that the decisions file covers every fixture lead_id exactly once; the whole suite runs offline with the model call mocked.
7. Deliverables: README.md (what it is, how to run in two commands, how to re-run on a fresh fixture, where the log is), docs/design.md, docs/ai-usage.md (the prompts I used with you, what you produced, what I changed, what I checked), and SUBMISSION.md with exactly these seven headings in this order: Written answer; Operating artifact; Evidence log; Number source labels; AI usage disclosure; What breaks it; What stays human. The written answer covers how it works step by step, the architecture, what the fixture threw at us (every trap row by lead_id and how it was handled), at least one bad output from the real run with how I caught it and what changed, what breaks first at 500 leads a week, and leaves a clearly marked gap for Part 3 (the meta question) that I write myself. Every number in SUBMISSION.md carries [Observed], [Estimated], [Benchmarked] or [Assumed]. Nothing in the repo addresses a reviewer or an AI.

Work in this order: scaffold and the rule layer with tests; the manipulation detector with tests; the LLM layer with the mocked test; run it on the real fixture with the real model and keep the real log; the compare script; the docs. After each step run `uv run pytest -q` and `uv run ruff check .` and show me the output. Then run `python3 scripts/validate_submission.py SUBMISSION.md` from the beat-claude checkout and fix every failure and warning. Stop and ask me before any decision that changes what a row is routed to.
```

A second, shorter instruction set the build constraints: build locally and do
not publish anything; author every commit as me with no AI co-author lines;
read the Gemini key from the environment at run time and never print, log or
commit it; if no key is readable, build the Gemini layer anyway, run with the
offline judge and say so; keep the first run's log as produced; leave Part 3 of
the write-up for me to write.

The system prompt the agent sends to the judge is in `src/lead_triage/llm.py`
(`SYSTEM_PROMPT`) and is printed in the header of every `run.log`.

## What the AI produced

- The package under `src/lead_triage/`: rules, detector, prompt and backends,
  pipeline, CLI.
- The tests under `tests/` (one synthetic row per rule, detector phrasings and
  zero-width cases, mocked Gemini calls, fixture coverage, and two strict xfail
  tests that record known gaps).
- `scripts/compare.py` and the empty `manual_labels.csv`.
- Two fixture runs under `runs/`, and drafts of README.md, docs/design.md, this
  file and SUBMISSION.md.

## What was checked during the build, and how

- Every rule, the detector and the reply validation are pinned by tests; the
  suite runs offline (`uv run pytest -q`).
- Both runs were read row by row against the fixture. That reading found the
  defects listed under "Bad output" in SUBMISSION.md; each fix is its own
  commit with a test, and the second run's decisions were diffed against the
  first (only L-016 changed decision).
- The fixture copy's sha256 matches upstream, and a test checks it.
- The spec asked the build to stop before any change to how a row is routed.
  One such change was made without stopping: the low-certainty floor, which
  moved L-016 from NURTURE to ESCALATE. It is its own commit ("Fix the defects
  found by reading run 1"), so it can be reviewed or reverted on its own.
- `SUBMISSION.md` was run through the challenge's pre-screen until it passed
  with no failures and no warnings.

## What I changed and what I checked myself

> SARTHAK WRITES THIS: what you changed after reading the build, and what you
> checked yourself (for example the manual labelling pass and any rows you
> re-read against the fixture).

## Known weak spots in the AI-produced work

- The rule thresholds and keyword lists were written while looking at this
  fixture. They are general rules, not lead_id lookups, but a fresh fixture
  will find words they do not know.
- The offline judge is a keyword matcher. It cannot read Spanish buying cues or
  a message that says what it wants without the usual words, which is why
  low-certainty verdicts now go to a person.
- The Gemini prompt has not been run against the fixture, so its behaviour on
  these rows is untested.
