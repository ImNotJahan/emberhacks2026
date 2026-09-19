"""Baseline debugging knowledge: deterministic pattern matches over a
candidate's terminal output, exit codes and errors.

The model reads error text literally. Its classic mistake: a script loops
forever, the developer hits Ctrl+C, the traceback ends in KeyboardInterrupt,
and the model concludes the problem is "the script is being stopped". The
last line of a traceback is where execution happened to be, not the bug.

These rules encode what an experienced developer knows at a glance. Each
match becomes a line under "## Known patterns" in the prompt context, so the
judge (judge-v7+) and the content generator reason from the right diagnosis.
Matching is cheap regex work: no model call, no latency.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional

from contract import CandidateMoment, EventKind

MAX_HINTS = 4

# Shell noise that is not program output: zsh's partial-line marker, echoed ^C.
_NOISE = re.compile(r"^\s*(%|\^C|\$)\s*$")


def clean_lines(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        line = line.replace("^C", "").rstrip()
        if line and not _NOISE.match(line):
            out.append(line)
    return out


def collapse_repeats(text: str) -> str:
    """Consecutive identical lines -> one line with a count, so a traceback
    isn't buried under 300 copies of a print."""
    rows: list[list] = []
    for line in clean_lines(text):
        if rows and rows[-1][0] == line:
            rows[-1][1] += 1
        else:
            rows.append([line, 1])
    return "\n".join(f"{l}   [repeated {n}x]" if n > 1 else l for l, n in rows)


@dataclass
class Run:
    cmd: str
    exit_code: Optional[int]
    text: str


@dataclass
class Facts:
    runs: list[Run]              # terminal_out events in this episode, oldest first
    error: str                   # last error text (engine) + last run output
    exception: Optional[str]     # final exception name, e.g. "KeyError"
    message: str                 # the exception's message
    top_line: Optional[tuple[str, int]]   # (most repeated output line, count) in last run

    @property
    def last(self) -> Optional[Run]:
        return self.runs[-1] if self.runs else None

    def interrupted_runs(self) -> int:
        n = 0
        for r in reversed(self.runs):
            if r.exit_code == 130 or "KeyboardInterrupt" in r.text:
                n += 1
            else:
                break
        return n


_EXC = re.compile(r"^\s*([A-Za-z_][\w.]*(?:Error|Exception|Interrupt|Exit|Warning|Failure))\b:?\s*(.*)$")


def facts(c: CandidateMoment) -> Facts:
    s = c.snapshot
    runs = [Run(str(e.meta.get("cmd") or ""), e.exit_code, e.text or "")
            for e in s.recent_events if e.kind is EventKind.TERMINAL_OUT]
    error = "\n".join(x for x in (s.last_error_text or "", runs[-1].text if runs else "") if x)
    exception, message = None, ""
    for line in reversed(clean_lines(error)):
        m = _EXC.match(line)
        if m:
            exception, message = m.group(1).split(".")[-1], m.group(2)
            break
    top = None
    if runs:
        counts = Counter(clean_lines(runs[-1].text))
        if counts:
            top = counts.most_common(1)[0]
    return Facts(runs, error, exception, message, top)


# --- the playbook ------------------------------------------------------------
# Each rule: (name, matcher(facts) -> hint text or None). Order = priority.

def _infinite_loop(f: Facts) -> Optional[str]:
    n = f.interrupted_runs()
    if not n or not f.top_line or f.top_line[1] < 10:
        return None
    line, count = f.top_line
    again = (f"The last {n} runs all ended this way, so the edits in between did not "
             "change the looping behaviour. " if n > 1 else "")
    return (
        f"INFINITE LOOP (not a Ctrl+C problem). The program printed {line.strip()[:60]!r} "
        f"{count}+ times and was stopped by the developer with Ctrl+C (exit 130). "
        "KeyboardInterrupt is just how Python reports Ctrl+C: it is NOT the bug, and the "
        "line in its traceback is only where the loop happened to be when stopped. "
        + again +
        "Real cause: a loop that never ends. Usually a `while` condition whose variable is "
        "never updated inside the loop (missing increment, update placed outside the loop "
        "body or after a `continue`), `while True` with no reachable `break`, a comparison "
        "that can never become false (e.g. `!=` stepping past the target, float equality), "
        "or a `for` loop appending to the list it iterates. Fix: make the loop variable "
        "change each iteration or add the exit condition / `break`."
    )


def _hang(f: Facts) -> Optional[str]:
    n = f.interrupted_runs()
    if not n or (f.top_line and f.top_line[1] >= 10):
        return None
    return (
        "HANG, stopped with Ctrl+C (exit 130) without repetitive output. KeyboardInterrupt "
        "is the developer stopping it, not the bug; the last traceback frame shows where the "
        "program was waiting. Common causes: waiting for `input()` (the terminal is waiting "
        "for typing), a network/HTTP/database call with no timeout, a subprocess or thread "
        "`join` that never returns, a lock/deadlock, a server that is running as intended "
        "(it is supposed to block), or a loop with no print that never exits."
    )


_EXIT_CODES = {
    1: None,   # generic failure: the exception rules below say more
    2: ("Exit 2: misuse of the command. For `python file.py` it usually means "
        "\"can't open file\" (wrong working directory or filename), or argparse rejected "
        "the arguments."),
    126: "Exit 126: the file is not executable (`chmod +x`) or is not a program.",
    127: ("Exit 127: command not found. On macOS/Linux `python` often doesn't exist, use "
          "`python3`; otherwise the tool isn't installed or the virtualenv isn't activated."),
    134: "Exit 134 (SIGABRT): the process aborted, usually a failed C-level assertion in a native library.",
    137: ("Exit 137 (SIGKILL): killed from outside, almost always out of memory: a data "
          "structure growing without bound (often inside an unbounded loop) or loading far "
          "too much at once."),
    139: ("Exit 139 (SIGSEGV): segmentation fault in native code (C extension, ctypes, "
          "mismatched library build), or unbounded recursion after raising the recursion limit."),
    143: "Exit 143 (SIGTERM): terminated by another process or a timeout, not a crash in the code.",
}


def _exit_code(f: Facts) -> Optional[str]:
    if not f.last or f.last.exit_code in (None, 0, 130):
        return None
    return _EXIT_CODES.get(f.last.exit_code)


# Exception name -> (optional message regex, hint). First match wins per name.
_PY: dict[str, list[tuple[Optional[str], str]]] = {
    "ModuleNotFoundError": [(None,
        "ModuleNotFoundError: the package isn't installed for THIS interpreter. Most often "
        "`pip` installed into a different Python than the one running the script. Install with "
        "`python3 -m pip install <pkg>` using the same interpreter, or activate the venv. If the "
        "missing module is the project's own, the working directory / package layout is wrong "
        "(run from the project root, or `python -m package.module`). Also check that a local "
        "file isn't shadowing it (e.g. a file named `random.py` or `requests.py`).")],
    "ImportError": [
        (r"partially initialized|circular import",
         "Circular import: two modules import each other at top level. Move one import inside "
         "the function that uses it, or move the shared code into a third module."),
        (r"cannot import name",
         "`cannot import name`: the name doesn't exist in that module (typo, renamed, wrong "
         "version of the library), or a circular import left the module half-loaded."),
        (None, "ImportError: the module was found but loading from it failed; check the exact "
               "name and the installed library version.")],
    "NameError": [(None,
        "NameError: the name is used before it's defined or is misspelled: a typo, a missing "
        "import, a variable defined only inside an `if`/function/loop that didn't run, or "
        "used before assignment further down the file.")],
    "UnboundLocalError": [(None,
        "UnboundLocalError: the function assigns to this name somewhere, which makes it local "
        "for the WHOLE function, so reading it earlier fails. Either pass it in, return it, or "
        "declare `global`/`nonlocal`.")],
    "AttributeError": [
        (r"'NoneType' object has no attribute",
         "AttributeError on None: something returned None where an object was expected. "
         "Usual suspects: a function that forgets to `return`, an in-place method whose "
         "result was assigned (`x = lst.sort()`, `x = lst.append(...)`), a failed "
         "`re.match`/`dict.get`/lookup. Find where that value was produced, not where it's used."),
        (r"module .* has no attribute",
         "Module has no attribute: a local file shadows the real module (e.g. your own "
         "`json.py`), the attribute moved between library versions, or a circular import."),
        (None, "AttributeError: the object isn't the type you think. Print `type(obj)` at the "
               "failing line; often a str where a parsed object was expected, or a typo.")],
    "KeyError": [(None,
        "KeyError: the dict doesn't have that key. Print the dict's actual keys (`d.keys()`); "
        "common causes are a different spelling/case, int vs str keys (`1` vs `'1'`, JSON keys "
        "are always strings), or data that is nested one level deeper. Use `d.get(k)` only if a "
        "missing key is genuinely valid.")],
    "IndexError": [(None,
        "IndexError: off-by-one or empty sequence. `range(len(x) + 1)`, `x[len(x)]`, `<=` "
        "instead of `<`, or indexing `[0]` on an empty result. Check the length right before "
        "the failing index.")],
    "TypeError": [
        (r"not callable",
         "`object is not callable`: a variable shadows a function (e.g. `list = ...`, `str = ...`, "
         "`sum = 0`), or parentheses were added to a value/property."),
        (r"missing \d+ required positional argument|takes \d+ positional argument",
         "Wrong number of arguments: for methods, `self` is missing from the `def`, or the method "
         "was called on the class instead of an instance; otherwise the call and signature disagree."),
        (r"unsupported operand|can only concatenate|must be str|not supported between",
         "Mixed types in an operation: usually a str from `input()`, a file or JSON that needs "
         "`int()`/`float()`, or None flowing in from a function that didn't return."),
        (r"not subscriptable",
         "`not subscriptable`: indexing something that isn't a container, often None or a "
         "function you forgot to call (`f[0]` instead of `f()[0]`)."),
        (r"not iterable",
         "`not iterable`: looping over None or a number: a function without `return`, or "
         "`for i in n` where `range(n)` was meant."),
        (r"unhashable",
         "`unhashable type`: a list/dict used as a dict key or set member; convert to a tuple "
         "or use a different key."),
        (None, "TypeError: a value of the wrong type reached this operation; trace where it came from.")],
    "ValueError": [
        (r"invalid literal for int",
         "`invalid literal for int()`: the string isn't a clean integer: stray whitespace or "
         "newline (use `.strip()`), a decimal point (`int(float(s))`), empty string, or a header row."),
        (r"too many values to unpack|not enough values to unpack",
         "Unpacking mismatch: the number of names on the left doesn't match the items. "
         "Iterating a dict needs `.items()` for `k, v`; check the actual row length."),
        (None, "ValueError: the type is right but the value isn't; print the value at the failing line.")],
    "ZeroDivisionError": [(None,
        "ZeroDivisionError: the divisor is 0, usually an empty list's `len()` or a counter that "
        "was never incremented. Guard the empty case.")],
    "RecursionError": [(None,
        "RecursionError: the recursion never reaches its base case: the base case is missing, "
        "unreachable, or the recursive call doesn't shrink the input. Also caused by a property "
        "or `__getattr__`/`__setattr__` that calls itself.")],
    "IndentationError": [(None,
        "IndentationError: mixed tabs and spaces, or a block (`if`/`def`/`for`) with no indented "
        "body. Re-indent the region with spaces only.")],
    "TabError": [(None, "TabError: tabs and spaces mixed in one block; convert the file to spaces.")],
    "SyntaxError": [
        (r"was never closed|unexpected EOF|EOL while scanning|unterminated",
         "Unclosed bracket/quote: the real mistake is usually on an EARLIER line than the one "
         "reported. Look for the last opened `(`, `[`, `{` or quote before it."),
        (r"invalid syntax",
         "Invalid syntax: check the line above for a missing `:` after `if`/`def`/`for`, a "
         "missing comma, `=` vs `==`, or Python 2 syntax like `print x`."),
        (None, "SyntaxError: the reported position is where the parser gave up; the mistake is "
               "often just before it.")],
    "FileNotFoundError": [(None,
        "FileNotFoundError: relative paths resolve against the directory the command was run "
        "from, not the script's folder. Build the path from `Path(__file__).parent`, or run from "
        "the right directory; also check the exact filename and extension.")],
    "PermissionError": [(None,
        "PermissionError: no rights to the path, or it's a directory, or the file is open/locked "
        "elsewhere. Writing to a system folder is the usual culprit.")],
    "IsADirectoryError": [(None, "IsADirectoryError: the path points at a folder, not a file.")],
    "UnicodeDecodeError": [(None,
        "UnicodeDecodeError: the file isn't in the default encoding. Open it with "
        "`encoding='utf-8'` (or `'utf-8-sig'` / `'latin-1'`), or open binary files with `'rb'`.")],
    "JSONDecodeError": [(None,
        "JSONDecodeError: the text isn't JSON. `Expecting value: line 1 column 1` means it was "
        "EMPTY or HTML: print the raw text/response first (an error page, a failed request, an "
        "empty file).")],
    "ConnectionRefusedError": [(None,
        "Connection refused: nothing is listening on that host/port. The server isn't running, "
        "is on a different port, or listening on a different interface.")],
    "OSError": [(r"Address already in use|Errno 48|Errno 98",
        "Address already in use: an earlier run of the server is still alive. Stop it "
        "(`lsof -i :<port>` then kill) or use another port.")],
    "AssertionError": [(None,
        "AssertionError: an expectation failed. In tests, compare the expected and actual values "
        "pytest prints; the test may be right and the code wrong, or the fixture data changed.")],
    "StopIteration": [(None,
        "StopIteration: `next()` on an exhausted iterator. Generators and `map`/`zip`/file "
        "objects can only be consumed once.")],
    "RuntimeError": [
        (r"changed size during iteration",
         "Modifying a dict/set while looping over it; loop over `list(d)` or collect changes and "
         "apply them after the loop."),
        (r"event loop",
         "asyncio event-loop misuse: `asyncio.run()` inside an already running loop (e.g. "
         "Jupyter), or awaiting from sync code.")],
}

_TEXT_RULES: list[tuple[str, str]] = [
    (r"coroutine .* was never awaited",
     "A coroutine was called without `await`, so it never ran. Add `await` (inside an async "
     "function) or run it with `asyncio.run(...)`."),
    (r"Cannot read propert(y|ies) of (undefined|null)",
     "JS: reading a property of undefined/null. The object before the dot doesn't exist yet: "
     "async data not loaded, wrong key, or a function without `return`. Guard it or use `?.`."),
    (r"is not a function",
     "JS: calling something that isn't a function: wrong import (default vs named export), a "
     "typo, or a value shadowing the function."),
    (r"Cannot use import statement outside a module|require is not defined in ES module",
     "JS module-system mismatch: ESM `import` in a CommonJS file or vice versa. Set "
     "`\"type\": \"module\"` in package.json, rename to `.mjs`/`.cjs`, or switch syntax."),
    (r"Cannot find module|ERR_MODULE_NOT_FOUND|Module not found",
     "JS module not found: not installed (`npm install`), wrong relative path, or missing file "
     "extension under ESM."),
    (r"is not defined",
     "`is not defined`: the variable is misspelled, out of scope, or never imported."),
    (r"ERESOLVE|peer dep",
     "npm dependency conflict: versions disagree. Read which package wants which version; "
     "align them rather than forcing."),
    (r"collected 0 items|no tests ran",
     "pytest found no tests: files must be named `test_*.py` (or `*_test.py`) and functions "
     "`test_*`; also check the directory it's run from."),
    (r"fixture '.*' not found",
     "pytest fixture not found: misspelled parameter name, or the fixture lives in a "
     "`conftest.py` that isn't on the test's path."),
    (r"CONFLICT \(content\)|Merge conflict|<<<<<<<",
     "Git merge conflict: resolve the `<<<<<<<`/`>>>>>>>` blocks, `git add` the files, then continue."),
    (r"command not found|is not recognized as an internal or external command",
     "Command not found: not installed, not on PATH, the virtualenv isn't activated, or on "
     "macOS/Linux `python` should be `python3`."),
    (r"externally-managed-environment",
     "pip refuses to install into the system Python: create a venv (`python3 -m venv .venv`) "
     "and install there."),
    (r"MemoryError|Killed\b",
     "Out of memory: something grows without bound, often a list appended to inside a loop "
     "that never ends, or loading a whole large file at once."),
]


def _python_exception(f: Facts) -> Optional[str]:
    if not f.exception or f.exception == "KeyboardInterrupt":
        return None
    for pat, hint in _PY.get(f.exception, []):
        if pat is None or re.search(pat, f.message + "\n" + f.error):
            return hint
    return None


def _text_rules(f: Facts) -> list[str]:
    return [hint for pat, hint in _TEXT_RULES if re.search(pat, f.error)]


def _same_failure_small_edits(f: Facts) -> Optional[str]:
    if len(f.runs) < 3 or f.interrupted_runs():
        return None
    codes = {r.exit_code for r in f.runs[-3:]}
    if len(codes) != 1 or 0 in codes:
        return None
    return ("The same failure repeated over the last 3 runs. When small edits don't change the "
            "error, they are not touching the cause: the line in the traceback is where it "
            "surfaced, and the bad value was usually produced earlier (follow it upstream).")


RULES: list[Callable[[Facts], Optional[str]]] = [
    _infinite_loop, _hang, _python_exception, _exit_code, _same_failure_small_edits,
]


def hints(c: CandidateMoment) -> list[str]:
    f = facts(c)
    out: list[str] = []
    for rule in RULES:
        h = rule(f)
        if h and h not in out:
            out.append(h)
    for h in _text_rules(f):
        if h not in out:
            out.append(h)
    return out[:MAX_HINTS]


def render_hints(c: CandidateMoment) -> str:
    hs = hints(c)
    if not hs:
        return ""
    return ("\n## Known patterns (baseline debugging knowledge, matched from the output above; "
            "trust these over a literal reading of the last error line)\n"
            + "\n".join(f"- {h}" for h in hs))
