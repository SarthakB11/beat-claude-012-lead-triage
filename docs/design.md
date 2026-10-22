# Design

The task is a first pass over a small queue of web-form leads, where some rows
are traps. The design follows from one question: which parts of the decision
can be checked exactly, and which need judgement? Exact checks go in code,
judgement goes to a model, and anything where a wrong automated call is costly
goes to a person.

## Flow

```text
CSV ──> schema check ──> per-row rules ──> cross-row rules ──> route()
                                                                 │
                     ┌───────────────────────────────────────────┼──────────────────┐
                     │ ESCALATE or REJECT by code                │ no deciding rule │
                     │ (judge never sees the row)                ▼                  │
                     │                         build prompt: lead JSON inside a     │
                     │                         random-boundary block, rule notes    │
                     │                         outside it                           │
                     │                                           │                  │
                     │                         judge (Gemini, or offline keywords)  │
                     │                                           │                  │
                     │                         validate reply in code; bad reply    │
                     │                         or certainty < 0.5 -> ESCALATE       │
                     └───────────────────────────────┬───────────┴──────────────────┘
                                                     ▼
                         confidence computed in code (cap 0.6 with any rule hit)
                                                     ▼
                              decisions.csv, decisions.json, run.log
```

## Choices and the reason for each

**Two layers, code first.** A missing email, an impossible date or a duplicate
address are facts, not opinions. Code finds them the same way every time, a
test can pin each one, and a model can neither miss nor talk itself out of
them. The model is used only for what code cannot do well: reading a free-text
message and deciding whether it describes a buyer.

**The model never sees a row that code already routed.** If a row is going to a
person anyway, a model call adds cost and a second opinion nobody acts on.
More important, the rows code routes include the adversarial ones. Not sending
them means the defence against prompt injection does not depend on the model
resisting it.

**Hard rules live in code, not in the prompt.** A prompt that says "escalate
rows with missing fields" is a request, not a guarantee. Rules in code are
deterministic and testable, and changing one shows up in a diff and a test.

**Routing precedence.** When several rules fire, manipulation and sensitive
content win (a person must see them whatever else is true), then obvious test
data (closed as REJECT so it does not take a person's time), then the other
escalation rules (missing, malformed, conflicting, unverifiable), and
duplicates last. A duplicate of a good lead is closed as REJECT with a pointer
to the original, because the lead is already in the queue and a second record
splits the history. The reason says to merge the note, so the follow-up's
content is not lost.

**Flags versus deciding rules.** Some findings should not route a row by
themselves: a budget written as "15k", a budget written as "we'll discuss",
HTML in the message, an email domain that differs from the website. These are
passed to the judge as trusted notes outside the data block, and they cap the
row's confidence at 0.6.

**Duplicates.** A later row is a duplicate of an earlier one when the
normalised email matches (lower case, plus-tags removed, dots removed for
Gmail), or when the normalised company matches (suffixes like Inc and Group
removed, spaces ignored) and the names match fuzzily ("D. Reyes" matches "Dana
Reyes"). If the same sender reappears with a different company, a different
budget or a different name, that is not a duplicate but a conflict, and it
goes to a person. Only the later row gets the hit, so the first submission is
still triaged on its merits.

**Conflicting signals.** Two cheap checks cover the common lies: a budget
under 1,000 USD a month from a company that claims thousands of employees or
an "unlimited budget", and a budget of 10,000 USD a month or more from a free
webmail address with no company named. Both are heuristics and can misfire,
so their confidence is 0.5 rather than 0.6.

**Sensitive content.** Keyword lists for legal threats, privacy requests
(GDPR, CCPA, deletion), payment details, third-party personal data, and
security bait (links to executables, "download the attached"). These are not
sales decisions. Each has an owner, and the reason names the next step, chosen
from the most severe category present (security before payment).

**Manipulation detector.** The detector looks for instruction overrides
("ignore ... previous instructions"), role play ("you are now", "act as"),
decision directives ("classify this lead as", "qualify this"), confidence
directives, the output vocabulary written in capitals, chat role tags
("system:", `<|im_start|>`, `[INST]`), zero-width and bidi characters, and
HTML comments. It runs on the raw cell values of every field, before cleaning,
because cleaning removes exactly what it is looking for. A hit routes the row
to ESCALATE. The matched sentence goes into the run log only, inside a fenced
block, with invisible characters rendered as `<U+200B>` so a reader sees them.
The reason in the decisions file names the kinds of pattern found, never the
text.

**The prompt treats the lead as data.** The lead is serialised as JSON and
placed between two boundary lines that carry a random token, generated per
call, so text in the lead cannot forge the closing line. Any "=====" inside
the data is broken up as well. The system prompt says the block is untrusted,
may contain instructions, must not be obeyed, and that an attempt to instruct
should be answered with ESCALATE. This is the second line of defence, for
injections the detector misses (for example the Spanish one recorded as a
known gap in the tests).

**Strict reply validation.** The Gemini call asks for JSON with a response
schema at temperature 0. Code still validates every field: the decision must
be one of four values, fit and intent from fixed lists, certainty a number
between 0 and 1, and a non-empty reason. Anything else becomes ESCALATE with
the validation error as the reason. A failed or rate-limited call (after
retries) also becomes ESCALATE, so a model outage never produces a confident
wrong answer.

**Low-certainty floor.** If the judge reports certainty below 0.5 for a
QUALIFY, NURTURE or REJECT, the row goes to a person. This was added after the
first fixture run, where the offline judge nurtured a lead it did not
understand (see SUBMISSION.md, bad output).

**Confidence is computed, not copied.** A model's self-reported certainty is
poorly calibrated, so it is one input, not the answer. For judged rows,
confidence is the certainty times a data-completeness factor (minus 0.1 for
each of company, website and numeric budget that is missing), clamped to
0.05 to 0.95, and capped at 0.6 if any rule fired. For rows code routed,
confidence is 0.6 for exact rules and 0.5 for heuristic ones. The cap at 0.6
means a downstream sorter can treat "above 0.6" as "no rule had anything to
say about this row".

**QUALIFY floor of 5,000 USD a month.** The brief does not give the agency's
minimum engagement, so this is an assumption, stated in the system prompt and
in the run log header. Change `QUALIFY_MIN_BUDGET` in `llm.py` to move it.

**An offline judge alongside Gemini.** The pipeline must run without network
access or a key: for tests, for a reviewer without credentials, and for the
build environment, which had no key. The offline judge answers in the same
JSON format from keyword lists. It reads only the cleaned lead fields, never
the prompt, so it cannot be steered, and it is deliberately simple. Its
limits are visible in the run log, and one of them produced the bad output
described in the submission.

**Every run is kept.** Each run writes to its own folder under `runs/`. The
first fixture run is committed as produced, next to the run after the fixes,
so the before and after can be compared line by line.

**Scope.** No database, no queue, no web UI, no CRM integration. The brief
asks for a working first pass over a 20-row file in a couple of hours. The
things this would need at real volume are listed under "What breaks it" in
SUBMISSION.md.
