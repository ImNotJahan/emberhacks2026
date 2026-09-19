"""Scripted evaluation sessions: realistic RAW editor traces + moment labels.

    python -m judge.scenarios          # (re)writes traces/eval/<name>.raw.jsonl + .labels.jsonl

Unlike judge/synth.py (which writes CandidateMoments directly), these are raw
observations in exactly the shape extension.js sends: edits carry sizes only,
terminal output keeps zsh's CRLF and PROMPT_SP tail, Ctrl+C exits 130. They
go through Person 1's capture engine like a live session, so the eval scores
capture AND judge together.

Labels are moments, not candidates: each marks a point where a nudge would
have helped (True) or would have been infuriating (False), whether or not the
capture engine nominated anything there. That is what lets `python -m judge.eval`
tell a capture miss from a judge miss.

These are stand-ins for recorded sessions, written from the scenario
catalogue in traces/eval/SCENARIOS.md. Replace or add to them with real
recordings (traces/eval/README.md), never quote them as real-world accuracy.

  stuck_import      stuck    20 min  circular import; error points at the wrong file,
                                     file thrash, 3-min stall, rage re-runs, then solved
  productive_tdd    product  22 min  red/green/refactor; same AssertionError 3x while the
                                     pass count climbs; multi-file rename; reading pauses
  mixed_pagination  mixed    26 min  flow → off-by-one thrash (edits flip ±1) → solved →
                                     flow → infinite loop Ctrl+C x3 → stall → solved
  explore_codebase  product  18 min  onboarding: many opens and jumps, few edits, one
                                     KeyError fixed first try
  env_hell          stuck    17 min  pip wheel build fails with every flag they try
  stuck_jest        stuck    16 min  TypeScript/jest: same TypeError, oscillating edits
                                     (single-language scope check)
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent / "traces" / "eval"

ZSH_TAIL = "\r\n%" + " " * 120 + "\r \r"      # what real zsh output ends with


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


class Session:
    def __init__(self, name: str, kind: str, t0: int, description: str) -> None:
        self.name, self.kind, self.description = name, kind, description
        self.t = t0
        self.rows: list[dict] = []
        self.labels: list[dict] = []
        self.rng = random.Random(name)
        self.cur: str | None = None
        self.overruns: list[str] = []

    # -- clock --------------------------------------------------------------
    def wait(self, s: float) -> "Session":
        self.t += int(s * 1000)
        return self

    def at(self, mmss: str) -> "Session":
        """Jump forward to an offset from the session start ("12:30")."""
        m, s = mmss.split(":")
        target = self.rows[0]["ts"] + (int(m) * 60 + int(s)) * 1000 if self.rows else self.t
        if target < self.t:                 # the script ran long: keep going, but say so
            self.overruns.append(f"{mmss} (+{(self.t - target) / 1000:.0f}s)")
        self.t = max(self.t, target)
        return self

    def _emit(self, k: str, **kw) -> None:
        row = {"k": k, "ts": self.t}
        row.update({x: v for x, v in kw.items() if v is not None})
        self.rows.append(row)

    @staticmethod
    def _lang(path: str) -> str:
        return {"py": "python", "ts": "typescript", "js": "javascript", "md": "markdown",
                "txt": "plaintext", "toml": "toml", "json": "json", "cfg": "ini"}.get(
                    path.rsplit(".", 1)[-1], "plaintext")

    # -- editor -------------------------------------------------------------
    def open(self, path: str) -> "Session":
        self._emit("file_open", path=path, lang=self._lang(path))
        return self.focus(path)

    def focus(self, path: str) -> "Session":
        self.cur = path
        self._emit("file_focus", path=path, lang=self._lang(path))
        return self.wait(self.rng.uniform(0.5, 2))

    def type(self, n: int, secs: float, big: bool = False, path: str | None = None) -> "Session":
        """A burst of n keystroke-level edits spread over `secs` seconds."""
        path = path or self.cur
        step = secs / max(n, 1)
        for _ in range(n):
            added = self.rng.choice([1, 1, 1, 2, 3, 0]) if not big else self.rng.randint(5, 60)
            removed = 0 if added else self.rng.choice([1, 1, 2])
            self._emit("edit", path=path, lang=self._lang(path),
                       meta={"added": added, "removed": removed})
            self.wait(step * self.rng.uniform(0.4, 1.6))
        return self

    def tweak(self, added: int, removed: int, path: str | None = None) -> "Session":
        """One small, exact edit: the +1/-1 flips that betray guessing."""
        path = path or self.cur
        self._emit("edit", path=path, lang=self._lang(path), meta={"added": added, "removed": removed})
        return self.wait(self.rng.uniform(1, 3))

    def save(self, path: str | None = None) -> "Session":
        path = path or self.cur
        self._emit("save", path=path, lang=self._lang(path))
        return self.wait(self.rng.uniform(0.3, 1.5))

    def jump(self, a: int, b: int, path: str | None = None) -> "Session":
        path = path or self.cur
        self._emit("cursor_jump", path=path, lang=self._lang(path), meta={"from": a, "to": b})
        return self.wait(self.rng.uniform(2, 8))

    def diag(self, count: int, msg: str = "", line: int = 0, path: str | None = None) -> "Session":
        path = path or self.cur
        if count:
            self._emit("diagnostic", path=path, lang=self._lang(path), text=msg,
                       meta={"count": count, "line": line})
        else:
            self._emit("diagnostic", path=path, lang=self._lang(path), meta={"count": 0})
        return self

    # -- terminal -------------------------------------------------------------
    def test(self, cmd: str, out: str, dur: float = 1.5, think: float = 3) -> "Session":
        """A pytest run; the exit code follows from the output like pytest's own."""
        code = 2 if "during collection" in out else 1 if re.search(r"\d+ failed", out) else 0
        return self.run(cmd, out, code, dur, think)

    def run(self, cmd: str, out: str, code: int, dur: float = 1.5, think: float = 3) -> "Session":
        assert isinstance(code, int), (self.name, cmd, code)
        is_test = any(w in cmd for w in ("pytest", "jest", "npm test", "npx jest"))
        self._emit("terminal_cmd", text=cmd, meta={"is_test": is_test})
        self.wait(dur)
        self._emit("terminal_out", text=_crlf(out) + ZSH_TAIL, exit_code=code,
                   meta={"cmd": cmd, "is_test": is_test})
        return self.wait(think)

    # -- ground truth ---------------------------------------------------------
    def label(self, should: bool, why: str, tol_s: int = 60) -> "Session":
        self.labels.append({"ts": self.t, "should_have_spoken": should,
                            "rationale": why, "tolerance_ms": tol_s * 1000})
        return self

    def write(self) -> None:
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        with open(EVAL_DIR / f"{self.name}.raw.jsonl", "w", encoding="utf-8") as fh:
            for r in self.rows:
                fh.write(json.dumps(r) + "\n")
        with open(EVAL_DIR / f"{self.name}.labels.jsonl", "w", encoding="utf-8") as fh:
            for lab in sorted(self.labels, key=lambda l: l["ts"]):
                fh.write(json.dumps(lab) + "\n")

    @property
    def minutes(self) -> float:
        return (self.rows[-1]["ts"] - self.rows[0]["ts"]) / 60000


# ---------------------------------------------------------------------------
# realistic tool output
# ---------------------------------------------------------------------------

def pytest_out(passed: int, failed: int, fail_name: str = "", fail_file: str = "",
               err: str = "", err_line: int = 0, body: str = "") -> str:
    total = passed + failed
    dots = "." * passed + "F" * failed
    head = (f"============================= test session starts ==============================\n"
            f"platform darwin -- Python 3.12.4, pytest-8.3.2, pluggy-1.5.0\n"
            f"rootdir: /Users/dev/shop\ncollected {total} items\n\n"
            f"{fail_file or 'tests/test_app.py'} {dots:<60}[100%]\n")
    if not failed:
        return head + f"\n============================== {passed} passed in 0.{passed + 11}s ===============================\n"
    etype = err.split(":")[0]
    return (head +
            f"\n=================================== FAILURES ===================================\n"
            f"_{'_' * 20} {fail_name} {'_' * 20}\n\n{body}"
            f"E       {err}\n\n{fail_file}:{err_line}: {etype}\n"
            f"=========================== short test summary info ============================\n"
            f"FAILED {fail_file}::{fail_name} - {err}\n"
            f"========================= {failed} failed, {passed} passed in 0.{total + 9}s =========================\n")


def pytest_collect_error(module: str, err: str, trace: str) -> str:
    return (f"============================= test session starts ==============================\n"
            f"platform darwin -- Python 3.12.4, pytest-8.3.2, pluggy-1.5.0\n"
            f"rootdir: /Users/dev/shop\ncollected 0 items / 1 error\n\n"
            f"==================================== ERRORS ====================================\n"
            f"_______________ ERROR collecting {module} _______________\n"
            f"{trace}E   {err}\n"
            f"=========================== short test summary info ============================\n"
            f"ERROR {module}\n!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
            f"=============================== 1 error in 0.21s ===============================\n")


def py_traceback(file: str, line: int, code: str, err: str, frames: str = "") -> str:
    return (f"Traceback (most recent call last):\n{frames}"
            f"  File \"/Users/dev/shop/{file}\", line {line}, in <module>\n    {code}\n{err}\n")


# ---------------------------------------------------------------------------
# 1. stuck: circular import
# ---------------------------------------------------------------------------

CIRC = ("ImportError: cannot import name 'Price' from partially initialized module "
        "'shop.pricing' (most likely due to a circular import) (/Users/dev/shop/shop/pricing.py)")
CIRC_TRACE = ("tests/test_models.py:1: in <module>\n    from shop.models import Product\n"
              "shop/models.py:3: in <module>\n    from shop.pricing import Price\n"
              "shop/pricing.py:2: in <module>\n    from shop.models import Product\n")


def stuck_import() -> Session:
    s = Session("stuck_import", "stuck", 1_800_100_000_000,
                "Adds a Product.price property; creates a models<->pricing import cycle. "
                "The traceback names pricing.py but the fix is to break the cycle.")
    s.open("shop/models.py").type(45, 70).save().wait(8)
    s.focus("tests/test_models.py").type(18, 30).save().wait(5)
    s.at("02:10").test("pytest tests/test_models.py", pytest_collect_error("tests/test_models.py", CIRC, CIRC_TRACE), 2)
    s.label(False, "First failure of a brand-new error right after writing code; they will read the traceback.")
    s.focus("shop/pricing.py").jump(2, 40).type(10, 25).save()
    s.at("03:10").test("pytest tests/test_models.py", pytest_collect_error("tests/test_models.py", CIRC, CIRC_TRACE), 2)
    s.focus("shop/models.py").type(6, 12).save()
    s.at("04:05").test("pytest tests/test_models.py", pytest_collect_error("tests/test_models.py", CIRC, CIRC_TRACE), 2)
    s.label(False, "Third identical failure, but each run follows a different edit in a different file: still ordinary debugging.")
    s.focus("shop/__init__.py").type(4, 8).save()
    s.at("05:00").test("pytest tests/test_models.py", pytest_collect_error("tests/test_models.py", CIRC, CIRC_TRACE), 2)
    s.label(True, "4th identical ImportError after edits in 3 different files. The cause is the cycle, not where they are editing: a one-line nudge naming models<->pricing would save minutes.", 75)
    # file thrash, almost no edits
    for p in ["shop/models.py", "shop/pricing.py", "shop/__init__.py", "shop/models.py",
              "shop/pricing.py", "shop/models.py"]:
        s.focus(p).wait(s.rng.uniform(8, 16))
    s.tweak(1, 1)
    s.at("06:40").run("python -c 'import shop.models'", py_traceback("shop/pricing.py", 2, "from shop.models import Product", CIRC), 1)
    s.label(True, "Bouncing between the same three files with one edit in 90s, then the same error again: lost, not exploring.", 60)
    s.wait(170)                           # alt-tabbed to a browser: no events
    s.at("09:40").label(True, "Nearly 3 minutes of silence after five identical failures: blocked (probably searching the error).", 90)
    s.focus("shop/models.py").type(12, 20).save()
    s.focus("shop/pricing.py").type(8, 15).save()
    s.at("11:00").test("pytest tests/test_models.py", pytest_collect_error("tests/test_models.py", CIRC, CIRC_TRACE), 2, think=1)
    for _ in range(4):                     # rage re-runs, no edits
        s.test("pytest tests/test_models.py", pytest_collect_error("tests/test_models.py", CIRC, CIRC_TRACE), 1.2, think=0.8)
    s.label(True, "Re-ran unchanged code 4 times in 10 seconds: hoping, not debugging.", 45)
    s.at("12:00").open("shop/types.py").type(60, 150, big=False).save()
    s.focus("shop/models.py").type(8, 15).save().focus("shop/pricing.py").type(8, 15).save()
    s.at("16:10").test("pytest tests/test_models.py", pytest_out(9, 3, "test_price_rounding", "tests/test_models.py",
                      "NameError: name 'Decimal' is not defined", 12), 1.5)
    s.label(False, "Error changed: they found and broke the cycle themselves. Interrupting now steals the win.")
    s.focus("shop/types.py").type(2, 4).save()
    s.at("16:45").test("pytest tests/test_models.py", pytest_out(10, 2, "test_price_rounding", "tests/test_models.py",
                      "AssertionError: assert Decimal('10.4') == Decimal('10.40')", 31), 1.5)
    s.type(5, 20).save()
    s.at("17:40").test("pytest tests/test_models.py", pytest_out(12, 0), 1.5)
    s.label(False, "Just went green. Nothing to add.")
    s.at("18:30").run("git add -A && git commit -m 'break models/pricing cycle'",
                      "[main 4e1f2a9] break models/pricing cycle\n 4 files changed, 31 insertions(+), 12 deletions(-)", 0, 0.3)
    s.wait(60).focus("shop/models.py").type(10, 30).save()
    s.at("20:00").focus("README.md")
    return s


# ---------------------------------------------------------------------------
# 2. productive: TDD
# ---------------------------------------------------------------------------

def productive_tdd() -> Session:
    s = Session("productive_tdd", "productive", 1_800_200_000_000,
                "Cart discounts by TDD. Every label False: any speech is a false positive. "
                "Contains the classic bait: same AssertionError 3x while pass count climbs.")
    s.open("tests/test_cart.py").type(40, 80).save()
    s.at("01:30").test("pytest tests/test_cart.py", pytest_out(11, 1, "test_discount_applies", "tests/test_cart.py",
                      "AttributeError: 'Cart' object has no attribute 'apply_discount'", 41), 1.2)
    s.label(False, "Red on purpose: the first step of TDD.")
    s.focus("shop/cart.py").type(35, 60).diag(1, "\"Decimal\" is not defined", 22).save()
    s.wait(2).diag(0)
    s.at("02:50").test("pytest tests/test_cart.py", pytest_out(11, 1, "test_discount_applies", "tests/test_cart.py",
                      "AssertionError: assert 90.0 == 81.0", 44), 1.2)
    s.type(8, 20).save()
    s.at("03:40").test("pytest tests/test_cart.py", pytest_out(11, 1, "test_discount_applies", "tests/test_cart.py",
                      "AssertionError: assert 90.0 == 81.0", 44), 1.2)
    s.label(False, "Second identical failure right after a real edit; ordinary.")
    s.type(5, 15).save()
    s.at("04:20").test("pytest tests/test_cart.py", pytest_out(12, 0), 1.2)
    # second cycle: stacking discounts, pass count climbs
    s.focus("tests/test_cart.py").type(30, 60).save()
    s.at("05:40").test("pytest tests/test_cart.py", pytest_out(12, 3, "test_stacked_discounts", "tests/test_cart.py",
                      "AssertionError: assert 72.0 == 64.8", 58), 1.2)
    s.focus("shop/cart.py").type(20, 40).save()
    s.at("06:40").test("pytest tests/test_cart.py", pytest_out(13, 2, "test_stacked_discounts", "tests/test_cart.py",
                      "AssertionError: assert 72.9 == 64.8", 58), 1.2)
    s.type(12, 25).save()
    s.at("07:30").test("pytest tests/test_cart.py", pytest_out(14, 1, "test_stacked_discounts", "tests/test_cart.py",
                      "AssertionError: assert 65.61 == 64.8", 58), 1.2)
    s.label(False, "Same AssertionError 3 runs in a row, but each follows a real edit and the pass count climbs 12→13→14: converging.", 45)
    s.type(6, 15).save()
    s.at("08:20").test("pytest tests/test_cart.py", pytest_out(15, 0), 1.2)
    s.label(False, "Green. A natural pause, but there is nothing wrong.")
    # reading docs mid-line: transient diagnostic open during a pause
    s.at("08:50").type(6, 8).diag(1, "Expected expression", 71)
    s.wait(150)
    s.label(False, "Paused 2.5 min with a half-written line flagged by Pylance: looking something up, not stuck.")
    s.type(8, 12).save().diag(0)
    # multi-file rename: diagnostics spike then fall
    s.at("12:00")
    for i, p in enumerate(["shop/cart.py", "shop/checkout.py", "shop/api.py", "shop/models.py",
                           "tests/test_cart.py", "tests/test_checkout.py"]):
        s.focus(p).type(9, 14).diag(max(0, 5 - i), "\"Discount\" is not defined", 10 + i).save()
    s.diag(0, path="shop/cart.py")
    s.label(False, "Six files in two minutes with diagnostics spiking: a planned rename, not file thrash.", 60)
    s.at("14:40").test("pytest", pytest_out(31, 0), 2.5)
    s.run("git add -A && git commit -m 'stacked discounts; rename Promo->Discount'",
          "[feat/discounts 9b2d110] stacked discounts; rename Promo->Discount\n 7 files changed, 118 insertions(+), 41 deletions(-)", 0, 0.3)
    s.label(False, "Committed: a coarse breakpoint, but nothing to say.")
    # third cycle: coupon expiry
    s.at("15:40").focus("tests/test_coupons.py").type(30, 55).save()
    s.at("16:50").test("pytest tests/test_coupons.py", pytest_out(0, 2, "test_expired_coupon", "tests/test_coupons.py",
                      "ModuleNotFoundError: No module named 'shop.coupons'", 1), 1.0)
    s.open("shop/coupons.py").type(40, 75).save()
    s.at("18:30").test("pytest tests/test_coupons.py", pytest_out(1, 1, "test_expired_coupon", "tests/test_coupons.py",
                      "TypeError: can't compare offset-naive and offset-aware datetimes", 19), 1.0)
    s.label(False, "Third red run in ~2 minutes on a brand-new test, and the error changed each time: normal TDD rhythm.")
    s.type(4, 10).save()
    s.at("19:10").test("pytest tests/test_coupons.py", pytest_out(2, 0), 1.0)
    s.at("19:40").test("pytest", pytest_out(33, 0), 2.5)
    s.run("git commit -am 'coupon expiry'", "[feat/discounts 1c77e04] coupon expiry\n 2 files changed, 64 insertions(+)", 0, 0.3)
    s.at("20:30").focus("shop/coupons.py").type(25, 80).save()
    s.at("22:00").focus("README.md")
    return s


# ---------------------------------------------------------------------------
# 3. mixed: off-by-one thrash, then an infinite loop
# ---------------------------------------------------------------------------

OBO = "AssertionError: assert [21, 22, 23, 24] == [21, 22, 23, 24, 25]"


def _hang(pages: int) -> str:
    lines = "".join("fetching page 2 (cursor=eyJpZCI6MjB9)...\n" for _ in range(pages))
    return (lines + "^CTraceback (most recent call last):\n"
            "  File \"/Users/dev/shop/scripts/fetch_all.py\", line 14, in <module>\n"
            "    resp = client.get(url, params={\"cursor\": cursor})\n"
            "  File \"/Users/dev/shop/.venv/lib/python3.12/site-packages/httpx/_client.py\", line 1054, in get\n"
            "KeyboardInterrupt\n")


def mixed_pagination() -> Session:
    s = Session("mixed_pagination", "mixed", 1_800_300_000_000,
                "Flow, then an off-by-one where the edits flip range bounds back and forth, "
                "then flow, then fetch_all loops forever (cursor never advanced).")
    s.open("shop/paginate.py").type(50, 90).save()
    s.focus("tests/test_paginate.py").type(30, 50).save()
    s.at("02:30").test("pytest tests/test_paginate.py", pytest_out(3, 1, "test_first_page", "tests/test_paginate.py",
                      "TypeError: paginate() missing 1 required positional argument: 'size'", 8), 1.0)
    s.label(False, "First failure mid-implementation.")
    s.focus("shop/paginate.py").type(4, 10).save()
    s.at("03:20").test("pytest tests/test_paginate.py", pytest_out(4, 0), 1.0)
    s.focus("tests/test_paginate.py").type(20, 40).save()
    s.at("06:00").test("pytest tests/test_paginate.py", pytest_out(4, 1, "test_last_page", "tests/test_paginate.py", OBO, 27), 1.0)
    s.focus("shop/paginate.py").jump(3, 18)
    flips = [(1, 1), (1, 1), (2, 2), (1, 1), (1, 1)]   # n-1 -> n+1 -> n -> n-1 ...
    for i, (a, r) in enumerate(flips):
        s.tweak(a, r).save()
        s.at(["07:00", "07:50", "08:40", "09:30", "10:20"][i]).test(
            "pytest tests/test_paginate.py", pytest_out(4, 1, "test_last_page", "tests/test_paginate.py", OBO, 27), 1.0)
        if i == 1:
            s.label(False, "Second failure; still reasonable to try the obvious fix.")
        if i == 3:
            s.label(True, "5 runs, same assertion, each edit is a 1-char flip back and forth: textbook guessing. "
                          "'The slice end is exclusive' is the whole answer.", 50)
        if i == 4:
            s.label(True, "6th identical failure and the edits are reverting each other.", 40)
    s.tweak(28, 0).save()                  # add debug prints
    s.at("11:00").test("pytest tests/test_paginate.py -s", "start=20 end=24 total=25\n" +
                      pytest_out(4, 1, "test_last_page", "tests/test_paginate.py", OBO, 27), 1.0)
    s.tweak(3, 30).save()
    s.at("11:50").test("pytest tests/test_paginate.py", pytest_out(5, 0), 1.0)
    s.label(False, "Solved it themselves. Don't take credit afterwards.")
    # flow
    s.at("12:30").open("shop/cursor.py").type(120, 200).save()
    s.label(False, "Long uninterrupted edit burst: deep in flow.")
    s.focus("tests/test_cursor.py").type(40, 60).save()
    s.at("17:00").test("pytest", pytest_out(11, 0), 2.0)
    # infinite loop
    s.open("scripts/fetch_all.py").type(35, 50).save()
    s.at("18:30").run("python scripts/fetch_all.py", _hang(40), 130, dur=12)
    s.label(False, "One hang, stopped with Ctrl+C: they already know it looped.")
    s.tweak(2, 1).save()
    s.at("19:40").run("python scripts/fetch_all.py", _hang(60), 130, dur=15)
    s.tweak(1, 2).save()
    s.at("20:40").run("python scripts/fetch_all.py", _hang(55), 130, dur=14)
    s.label(True, "Third infinite loop in a row after tiny edits: the cursor is never reassigned inside the loop. "
                  "Naming that saves minutes.", 60)
    for a, b in [(10, 14), (14, 9), (9, 22), (22, 14)]:
        s.jump(a, b)
    s.wait(80)
    s.at("22:40").label(True, "Stalled about 2 minutes staring at the loop after 3 hangs.", 70)
    s.tweak(24, 0).save()
    s.at("23:20").run("python scripts/fetch_all.py", "".join(f"fetching page {i}...\n" for i in range(1, 8)) +
                      "done: 7 pages, 131 orders\n", 0, dur=3)
    s.at("24:00").test("pytest", pytest_out(11, 0), 2.0)
    s.run("git commit -am 'cursor pagination + fetch_all'",
          "[main 70aa3e1] cursor pagination + fetch_all\n 5 files changed, 142 insertions(+)", 0, 0.3)
    s.label(False, "Committed. Done.")
    s.at("25:20").focus("README.md").type(20, 40).save()
    s.at("26:20").focus("shop/cursor.py")
    return s


# ---------------------------------------------------------------------------
# 4. productive: exploring an unfamiliar codebase
# ---------------------------------------------------------------------------

def explore_codebase() -> Session:
    s = Session("explore_codebase", "productive", 1_800_400_000_000,
                "Day-one onboarding. Lots of opens, jumps, reading; every label False.")
    s.run("git log --oneline | head -20", "\n".join(f"{i:07x} commit {i}" for i in range(20)), 0, 0.2, think=10)
    s.open("README.md").wait(40)
    for p in ["app/__main__.py", "app/config.py", "app/server.py", "app/routes/orders.py",
              "app/routes/users.py", "app/services/billing.py", "app/db.py", "app/models.py"]:
        s.open(p).jump(1, s.rng.randint(30, 200)).wait(s.rng.uniform(5, 15))
    s.label(False, "Opened 8 files in about 2 minutes with no edits: reading, not lost.", 60)
    s.wait(60).focus("app/services/billing.py")
    for a, b in [(12, 88), (88, 140), (140, 31), (31, 202)]:
        s.jump(a, b).wait(20)
    s.run("grep -rn 'def charge' app/", "app/services/billing.py:88:def charge(account, cents):\n"
          "app/routes/orders.py:41:    billing.charge(acct, total)", 0, 0.2, think=15)
    s.focus("app/routes/orders.py").jump(1, 41).wait(45)
    s.label(False, "Cursor jumping along a call chain with grep: tracing on purpose.")
    s.at("07:30").run("python -m app serve", py_traceback("app/config.py", 9, "DB = os.environ['DATABASE_URL']",
                      "KeyError: 'DATABASE_URL'", "  File \"<frozen runpy>\", line 198, in _run_module_as_main\n"), 1, 0.8, think=8)
    s.label(False, "First error, and the message says exactly what is missing.")
    s.open(".env.example").wait(20)
    s.run("cp .env.example .env && python -m app serve", "INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)\n"
          "^CINFO:     Shutting down\n", 130, dur=40, think=10)
    s.label(False, "Stopped a server with Ctrl+C on purpose: exit 130 is not a failure here.", 45)
    s.wait(150)
    s.at("11:30").label(False, "Long quiet stretch after the app came up: reading, nothing wrong on screen.", 90)
    for p in ["app/services/billing.py", "app/models.py", "app/services/billing.py", "tests/test_billing.py",
              "app/services/billing.py", "app/models.py"]:
        s.focus(p).wait(s.rng.uniform(10, 20))
    s.label(False, "Alternating between billing.py and models.py while comparing them; no error present.", 60)
    s.at("14:30").focus("tests/test_billing.py").type(25, 60).save()
    s.at("15:50").test("pytest tests/test_billing.py -k charge", pytest_out(6, 0, fail_file="tests/test_billing.py"), 1.5)
    s.at("16:30").open("NOTES.md").type(30, 70).save()
    s.at("18:10").focus("app/server.py")
    return s


# ---------------------------------------------------------------------------
# 5. stuck: environment (non-code) errors
# ---------------------------------------------------------------------------

PG = ("      Error: pg_config executable not found.\n\n"
      "      pg_config is required to build psycopg2 from source.  Please add the directory\n"
      "      containing pg_config to the $PATH or specify the full executable path with the\n"
      "      option:\n\n          python setup.py build_ext --pg-config /path/to/pg_config build ...\n\n"
      "      [end of output]\n\n"
      "  note: This error originates from a subprocess, and is likely not a problem with pip.\n"
      "error: metadata-generation-failed\n\n× Encountered error while generating package metadata.\n"
      "╰─> See above for output.\n")


def env_hell() -> Session:
    s = Session("env_hell", "stuck", 1_800_500_000_000,
                "Setting up a repo: psycopg2 won't build. Every attempt fails the same way; "
                "`psycopg2-binary` or `brew install libpq` is the answer.")
    s.open("README.md").wait(30)
    s.run("python3 -m venv .venv && source .venv/bin/activate", "", 0, 3, think=2)
    s.at("01:00").run("pip install -r requirements.txt", "Collecting psycopg2==2.9.9\n  Downloading psycopg2-2.9.9.tar.gz (384 kB)\n"
                      "  Preparing metadata (setup.py) ... error\n" + PG, 1, 9)
    s.label(False, "First install failure: they will read the message.")
    s.open("requirements.txt").wait(20)
    s.at("02:30").run("pip install --upgrade pip setuptools wheel", "Successfully installed pip-24.2 setuptools-74.1.2 wheel-0.44.0", 0, 6)
    s.run("pip install -r requirements.txt", "Collecting psycopg2==2.9.9\n  Using cached psycopg2-2.9.9.tar.gz (384 kB)\n"
          "  Preparing metadata (setup.py) ... error\n" + PG, 1, 7)
    s.label(False, "Second failure after a sensible first fix (upgrading pip): still reasonable.")
    s.at("04:30").run("pip install --no-cache-dir -r requirements.txt", "Collecting psycopg2==2.9.9\n  Downloading psycopg2-2.9.9.tar.gz (384 kB)\n"
                      "  Preparing metadata (setup.py) ... error\n" + PG, 1, 9)
    s.at("05:40").run("rm -rf .venv && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt",
                      "Collecting psycopg2==2.9.9\n  Downloading psycopg2-2.9.9.tar.gz (384 kB)\n"
                      "  Preparing metadata (setup.py) ... error\n" + PG, 1, 14)
    s.label(True, "4th identical pg_config failure, now nuking the venv: blind retries. "
                  "'Use psycopg2-binary or brew install libpq' is the whole answer.", 60)
    s.wait(150)
    s.at("09:00").label(True, "Quiet for 3 minutes after 4 failures: searching, blocked.", 90)
    s.run("pip install psycopg2", "Collecting psycopg2\n  Downloading psycopg2-2.9.9.tar.gz (384 kB)\n"
          "  Preparing metadata (setup.py) ... error\n" + PG, 1, 8)
    s.run("sudo pip install -r requirements.txt", "Password:\nsudo: a password is required\n", 1, 4)
    s.at("10:30").label(True, "Escalating to sudo pip after 5 identical build failures: about to make it worse.", 45)
    s.at("11:20").run("brew install postgresql", "==> Downloading https://ghcr.io/v2/homebrew/core/postgresql/16/manifests/16.4\n"
                      "==> Installing postgresql@16\n==> Summary\n🍺  /opt/homebrew/Cellar/postgresql@16/16.4: 3,812 files, 71.2MB\n", 0, 60)
    s.label(False, "Found the real fix and is waiting on a long install: don't interrupt a download.", 60)
    s.run("pip install -r requirements.txt", "Successfully installed psycopg2-2.9.9 fastapi-0.114.0 uvicorn-0.30.6", 0, 20)
    s.at("13:30").test("pytest", pytest_out(24, 0, fail_file="tests/test_api.py"), 4)
    s.label(False, "Everything installs and passes. Stay quiet.")
    s.at("14:30").open("app/main.py").type(30, 90).save()
    s.at("17:00").focus("app/main.py")
    return s


# ---------------------------------------------------------------------------
# 6. stuck: TypeScript / jest (single-language scope check)
# ---------------------------------------------------------------------------

def jest_fail(passed: int) -> str:
    return (" FAIL  src/cart.test.ts\n  Cart\n    ✓ adds items (3 ms)\n    ✕ lists line totals (4 ms)\n\n"
            "  ● Cart › lists line totals\n\n"
            "    TypeError: Cannot read properties of undefined (reading 'map')\n\n"
            "      12 |   lineTotals(): number[] {\n    > 13 |     return this.lines.map(l => l.qty * l.price);\n"
            "         |                       ^\n\n      at Cart.lineTotals (src/cart.ts:13:23)\n"
            "      at Object.<anonymous> (src/cart.test.ts:18:17)\n\n"
            f"Tests:       1 failed, {passed} passed, {passed + 1} total\nTime:        1.204 s\n")


def stuck_jest() -> Session:
    s = Session("stuck_jest", "stuck", 1_800_600_000_000,
                "TypeScript: `this.lines` is undefined because the class field is declared but "
                "the constructor assigns `this.items`. Checks the capture engine beyond Python.")
    s.open("src/cart.ts").type(40, 80).save()
    s.focus("src/cart.test.ts").type(25, 45).save()
    s.at("02:30").run("npx jest src/cart.test.ts", jest_fail(4), 1, 3)
    s.label(False, "First failure of a new test.")
    s.focus("src/cart.ts").jump(13, 4)
    for i, (a, r) in enumerate([(3, 1), (1, 3), (6, 0), (0, 6), (2, 2)]):
        s.tweak(a, r).save()
        s.at(["03:40", "04:40", "05:40", "06:40", "07:40"][i]).run("npx jest src/cart.test.ts", jest_fail(4), 1, 3)
        if i == 2:
            s.label(True, "4th identical TypeError; the edits add and remove the same `?.` guard.", 50)
        if i == 4:
            s.label(True, "6th identical failure; edits are cancelling each other out.", 50)
    for p in ["src/cart.ts", "src/cart.test.ts", "src/types.ts", "src/cart.ts", "src/cart.test.ts"]:
        s.focus(p).wait(s.rng.uniform(10, 20))
    s.wait(120)
    s.at("11:00").label(True, "Stalled after 6 failures, cycling through the same three files.", 90)
    s.focus("src/cart.ts").tweak(4, 4).save()
    s.at("11:40").run("npx jest src/cart.test.ts", " PASS  src/cart.test.ts\n  Cart\n    ✓ adds items (2 ms)\n"
                      "    ✓ lists line totals (1 ms)\n\nTests:       5 passed, 5 total\nTime:        1.011 s\n", 0, 3)
    s.label(False, "Found it (constructor assigned the wrong field). Quiet.")
    s.at("12:30").type(60, 150).save()
    s.at("15:00").run("npx jest", " PASS  src/cart.test.ts\n PASS  src/checkout.test.ts\n\nTests:       14 passed, 14 total\n", 0, 4)
    s.at("16:00").focus("src/checkout.ts")
    return s


SESSIONS = [stuck_import, productive_tdd, mixed_pagination, explore_codebase, env_hell, stuck_jest]


def write_manifest(sessions: list[Session]) -> None:
    path = EVAL_DIR / "manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {}
    for s in sessions:
        manifest[s.name] = {"kind": s.kind, "source": "scripted", "description": s.description}
    path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    built = []
    for f in SESSIONS:
        s = f()
        s.write()
        built.append(s)
        n_true = sum(l["should_have_spoken"] for l in s.labels)
        print(f"{s.name:<18} {s.kind:<10} {s.minutes:5.1f} min  {len(s.rows):4d} raw  "
              f"{len(s.labels):2d} labels ({n_true} speak)"
              + (f"  overran: {', '.join(s.overruns)}" if s.overruns else ""))
    write_manifest(built)
