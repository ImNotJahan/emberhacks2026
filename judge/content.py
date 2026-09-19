"""Content generator: what to actually say. Runs only after the judge has
decided to speak, as a separate call with its own prompt, so a bad message
and a badly-timed message are debuggable independently."""

from __future__ import annotations

import re

from contract import CandidateMoment
from judge import gemini
from judge.render import render_candidate

CONTENT_VERSION = "content-v4"

CONTENT_SYSTEM = """\
You write the one interruption a coding assistant is allowed to make. A
separate judge has already decided that now is worth interrupting; you only
decide WHAT to say.

Write ONE sentence (two at most), under 40 words, to the developer ("you").
It must be specific to their actual code: name the file, function, line,
error, or test from the context. Point at the concrete thing they have not
tried or noticed, and end on ONE concrete next action: the edit to make
(which line, which import to move or defer, which value to inspect) or the
one check that would settle it.

Do not restate the diagnosis the error message already gives them (e.g.
"there is a circular import between A and B"). They can read it; they have
been staring at it. Your value is the step they have not taken.

GROUNDING: name only identifiers (functions, variables, line numbers) that
appear verbatim in the context above. Real traces often contain no code at
all, only file names, errors and test results. Then anchor on those: the
file, the exact error, the failing test, and the concrete check to run.
Never guess a function or variable name; an invented name is worse than
saying nothing.

DIAGNOSE LIKE A SENIOR ENGINEER (content-v4):
- The last line of a traceback is where execution WAS, not necessarily the
  bug. Read the whole run: exit code, what was printed, how it ended.
- KeyboardInterrupt / exit 130 / ^C means the developer stopped the program
  themselves. Never tell them the problem is that it "is being stopped" or
  to "let it complete". Ask why it didn't finish: the same line printed
  over and over means an infinite loop; little output means it is waiting
  (input(), network, lock) or slow.
- Same failure after several small edits: the edits aren't touching the
  cause. Point upstream, to where the bad value or the loop condition is
  produced, not to the line that reports it.
- A "## Known patterns" section, when present, is a reliable diagnosis from
  deterministic matching. Build the message on it, and make the next action
  the specific fix it names (e.g. which loop condition to check, which
  variable must change each iteration), anchored on the file and line.
- Prefer the root cause and one concrete fix over generic advice.

Never: greet, apologize, say "it looks like", suggest a break, tell them to
"read the error" or "add logging" generically, or restate what they already
know. No markdown, no code blocks; inline identifiers in backticks are fine.
"""


def ungrounded(text: str, context: str) -> list[str]:
    """Backticked names in `text` that never appear in `context`. Splits
    `file.py:31` / `obj.attr` / `fn()` so reformatted facts still count."""
    bad = []
    for span in re.findall(r"`([^`]+)`", text):
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", span):
            if tok not in context:
                bad.append(tok)
    return bad


def generate(candidate: CandidateMoment, judge_reasoning: str,
             trajectory: str) -> gemini.CallResult:
    """One content call, plus one retry if it names something not in the
    context. A second invention returns data=None: the judge then stays
    silent rather than say something made up."""
    context = render_candidate(candidate, hints=True)
    user = (
        context
        + f"\n\n## Judge's read\ntrajectory={trajectory}\n{judge_reasoning}"
        + "\n\nWrite the interruption."
    )
    res = gemini.call(CONTENT_SYSTEM, user, temperature=0.3, max_output_tokens=120)
    bad = ungrounded(res.data or "", context + judge_reasoning)
    if bad:
        retry = gemini.call(
            CONTENT_SYSTEM,
            user + f"\n\nYour last draft named {', '.join(sorted(set(bad)))}, which do not "
                   "appear in the context. Rewrite using only names that do.",
            temperature=0.0, max_output_tokens=120)
        retry.latency_ms += res.latency_ms
        if ungrounded(retry.data or "", context + judge_reasoning):
            retry.data = None
        res = retry
    return res
