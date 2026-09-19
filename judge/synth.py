"""Synthetic labeled traces — PLACEHOLDERS until Person 4's recorded traces land.

    python -m judge.synth        # (re)writes traces/*.candidates.jsonl + *.labels.jsonl

Each trace is a sequence of CandidateMoments (contract.to_json, one per line)
with a parallel file of LabeledMoments at the same ts. Scenarios:

  productive (every label False — any speech is a false positive)
    refactor    deliberate rename fanning out over 6 files; diagnostics spike
                then fall; trips FILE_THRASH, REPEATED_ERROR, TEST_LOOP
    tdd         test loop whose failing count shrinks 5→4→2→1→0
    exploring   reading an unfamiliar codebase; jumps, opens, long idles
    parser      the contract's ambiguous example: same error 3x, different edits
  stuck (late labels True)
    thrash      same KeyError 6 runs, edits oscillate and revert
    blocked     circular import, same failure, then a long stall
    p1_stuck    Person 1's stuck.raw.jsonl through their capture engine:
                the only trace with real editor event shapes
  budget30      30 candidates in 18 minutes of a bad afternoon, for the
                interruption-budget check (not used for calibration)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from contract import (
    ActivitySnapshot,
    BudgetState,
    CandidateMoment,
    Episode,
    Event,
    EventKind,
    LabeledMoment,
    Signal,
    SignalKind,
    to_json,
)

TRACE_DIR = Path(__file__).resolve().parent.parent / "traces"
K = EventKind
S = SignalKind


class Trace:
    def __init__(self, name: str, t0: int, session_elapsed_ms: int = 20 * 60_000) -> None:
        self.name = name
        self.t = t0
        self.session_start = t0 - session_elapsed_ms
        self.ep: Optional[Episode] = None
        self.ep_events: list[Event] = []
        self.cands: list[CandidateMoment] = []
        self.labels: list[LabeledMoment] = []
        self._n_ep = 0

    def episode(self, primary: str) -> "Trace":
        self._n_ep += 1
        self.ep = Episode(episode_id=f"ep_{self.name}{self._n_ep:02d}",
                          started_ms=self.t, primary_path=primary)
        self.ep_events = []
        return self

    def ev(self, dt_s: float, kind: EventKind, path: Optional[str] = None, **kw) -> "Trace":
        self.t += int(dt_s * 1000)
        assert self.ep is not None
        e = Event(kind, self.t, self.ep.episode_id, path=path,
                  lang="python" if path and path.endswith(".py") else None, **kw)
        self.ep_events.append(e)
        self.ep.event_count += 1
        return self

    def wait(self, dt_s: float) -> "Trace":
        self.t += int(dt_s * 1000)
        return self

    def cand(self, signals: list[tuple], label: bool, why: str,
             error: Optional[str] = None, tests: Optional[str] = None) -> "Trace":
        assert self.ep is not None
        edits = [e.ts for e in self.ep_events if e.kind == K.EDIT]
        opened = []
        for e in self.ep_events:
            if e.path and e.path not in opened and e.kind in (K.FILE_OPEN, K.FILE_FOCUS, K.EDIT):
                opened.append(e.path)
        ep = Episode(**{**self.ep.__dict__})
        snap = ActivitySnapshot(
            snapshot_id=f"snap_{self.name}_{len(self.cands):02d}", ts=self.t, episode=ep,
            recent_events=list(self.ep_events[-80:]), open_paths=opened[-6:],
            last_error_text=error, last_test_summary=tests,
            seconds_since_last_edit=(self.t - edits[-1]) / 1000 if edits else 0.0,
            session_elapsed_ms=self.t - self.session_start,
        )
        sigs = [Signal(k, st, detail, self.t - int(age * 1000), occurrences=occ)
                for (k, st, detail, occ, age) in signals]
        self.cands.append(CandidateMoment(
            candidate_id=f"cand_{self.name}_{len(self.cands):02d}", ts=self.t,
            snapshot=snap, signals=sigs, budget=BudgetState()))
        self.labels.append(LabeledMoment(self.t, label, why, tolerance_ms=1000))
        return self

    def write(self) -> None:
        TRACE_DIR.mkdir(exist_ok=True)
        (TRACE_DIR / f"{self.name}.candidates.jsonl").write_text(
            "".join(to_json(c) + "\n" for c in self.cands), encoding="utf-8")
        (TRACE_DIR / f"{self.name}.labels.jsonl").write_text(
            "".join(to_json(l) + "\n" for l in self.labels), encoding="utf-8")


T0 = 1_789_000_000_000


# ---------------------------------------------------------------------------
# productive
# ---------------------------------------------------------------------------

def refactor() -> Trace:
    tr = Trace("refactor", T0).episode("app/models.py")
    fp = "NameError:UserRecord"
    tr.ev(0, K.FILE_OPEN, "app/models.py")
    tr.ev(8, K.EDIT, "app/models.py", text="class Account(BaseModel):  # was UserRecord")
    tr.ev(3, K.SAVE, "app/models.py")
    tr.ev(2, K.DIAGNOSTIC, "app/api/users.py", error_fingerprint=fp,
          text="14 problems: Undefined name 'UserRecord' (6 files)")
    tr.ev(6, K.FILE_FOCUS, "app/api/users.py")
    tr.ev(10, K.EDIT, "app/api/users.py", text="def get_user(id) -> Account:")
    tr.ev(9, K.EDIT, "app/api/users.py", text="return Account.from_row(row)")
    tr.ev(4, K.DIAGNOSTIC, "app/services/billing.py", error_fingerprint=fp,
          text="11 problems: Undefined name 'UserRecord' (5 files)")
    tr.ev(5, K.FILE_FOCUS, "app/models.py")
    tr.ev(6, K.EDIT, "app/models.py", text="UserRecord = Account  # temporary alias, remove after sweep")
    tr.ev(7, K.FILE_FOCUS, "app/services/billing.py")
    tr.ev(12, K.EDIT, "app/services/billing.py", text="def charge(account: Account, cents: int):")
    tr.ev(4, K.DIAGNOSTIC, "app/services/billing.py", text="8 problems (4 files)")
    tr.cand([(S.FILE_THRASH, 0.7, "app/models.py focused 3x in 90s", 3, 80),
             (S.REPEATED_ERROR, 0.6, "NameError:UserRecord diagnostics 2x", 2, 70)],
            False, "Mid-rename; diagnostics already falling 14→8.",
            error="Undefined name 'UserRecord'")
    tr.ev(6, K.FILE_FOCUS, "app/services/auth.py")
    tr.ev(9, K.EDIT, "app/services/auth.py", text="account = Account.by_email(email)")
    tr.ev(5, K.FILE_FOCUS, "app/models.py")
    tr.ev(4, K.CURSOR_JUMP, "app/models.py", text="line 12 -> 140")
    tr.ev(8, K.EDIT, "app/models.py", text="def by_email(cls, email) -> 'Account':")
    tr.ev(6, K.FILE_FOCUS, "app/api/admin.py")
    tr.ev(10, K.EDIT, "app/api/admin.py", text="from app.models import Account")
    tr.ev(4, K.DIAGNOSTIC, "app/api/admin.py", text="5 problems (3 files)")
    tr.ev(3, K.TEST_RUN, "tests/", exit_code=1, tests_passed=81, tests_failed=7,
          error_fingerprint=fp)
    tr.cand([(S.FILE_THRASH, 0.85, "app/models.py focused 5x in 2min", 5, 110),
             (S.REPEATED_ERROR, 0.7, "NameError:UserRecord seen 3x", 3, 150),
             (S.TEST_LOOP, 0.4, "first run: 7 failing", 1, 5)],
            False, "Refactor sweep; 7 failures are the unrenamed call sites.",
            error="NameError: name 'UserRecord' is not defined\n  File \"app/workers/sync.py\", line 22",
            tests="81 passed, 7 failed")
    tr.ev(8, K.FILE_OPEN, "app/workers/sync.py")
    tr.ev(9, K.EDIT, "app/workers/sync.py", text="for acct in Account.stale():")
    tr.ev(6, K.FILE_FOCUS, "tests/test_users.py")
    tr.ev(11, K.EDIT, "tests/test_users.py", text="acct = Account(email='a@b.c')")
    tr.ev(4, K.TEST_RUN, "tests/", exit_code=1, tests_passed=85, tests_failed=3,
          error_fingerprint=fp)
    tr.ev(5, K.FILE_FOCUS, "app/models.py")
    tr.ev(7, K.EDIT, "app/models.py", text="# removed: UserRecord = Account")
    tr.ev(3, K.TEST_RUN, "tests/", exit_code=1, tests_passed=84, tests_failed=4,
          error_fingerprint=fp)
    tr.cand([(S.FILE_THRASH, 0.9, "app/models.py focused 6x in 2min", 6, 100),
             (S.REPEATED_ERROR, 0.8, "NameError:UserRecord seen 5x", 5, 240),
             (S.TEST_LOOP, 0.6, "3 runs, failures 7→3→4", 3, 40)],
            False, "Removed alias on purpose to surface remaining call sites.",
            error="NameError: name 'UserRecord' is not defined\n  File \"tests/test_billing.py\", line 9",
            tests="84 passed, 4 failed (test_billing.py x4)")
    tr.ev(6, K.FILE_FOCUS, "tests/test_billing.py")
    tr.ev(10, K.EDIT, "tests/test_billing.py", text="from app.models import Account")
    tr.ev(4, K.TEST_RUN, "tests/", exit_code=0, tests_passed=88, tests_failed=0)
    tr.ev(20, K.GIT, text="commit: Rename UserRecord -> Account")
    tr.cand([(S.EPISODE_BOUNDARY, 0.5, "tests green then commit", 1, 1),
             (S.FILE_THRASH, 0.6, "6 files touched in 5 min", 6, 300)],
            False, "Done and committed; nothing to add.", tests="88 passed")
    return tr


def tdd() -> Trace:
    tr = Trace("tdd", T0 + 10_000_000).episode("src/cart.py")
    runs = [(5, "AssertionError:test_totals", "assert total == 1998"),
            (4, "AssertionError:test_discount", "assert 1798 == 1799"),
            (2, "TypeError:Decimal+float", "TypeError: unsupported operand type(s) for +: 'Decimal' and 'float'"),
            (1, "AssertionError:test_tax_rounding", "assert Decimal('2.07') == Decimal('2.08')"),
            (0, None, None)]
    edits = ["def total(self): return sum(i.price * i.qty for i in self.items)",
             "discount = (subtotal * pct / 100).quantize(CENT)",
             "shipping = Decimal(str(self.shipping))",
             "tax = (taxable * rate).quantize(CENT, rounding=ROUND_HALF_UP)"]
    tr.ev(0, K.FILE_OPEN, "tests/test_cart.py")
    tr.ev(20, K.EDIT, "tests/test_cart.py", text="def test_totals(): ... (5 new tests)")
    for i, (fails, fp, err) in enumerate(runs):
        tr.ev(12, K.TEST_RUN, "tests/test_cart.py", exit_code=1 if fails else 0,
              tests_passed=5 - fails, tests_failed=fails, error_fingerprint=fp)
        if i in (1, 2, 3):
            hist = " → ".join(str(r[0]) for r in runs[: i + 1])
            tr.cand([(S.TEST_LOOP, 0.5 + 0.1 * i, f"{i+1} runs, still failing ({hist})", i + 1, 90),
                     (S.REPEATED_ERROR, 0.4, "AssertionError in test_cart.py repeatedly", i + 1, 90)],
                    False, "Red-green loop, failing count shrinking every run.",
                    error=err, tests=f"{5-fails} passed, {fails} failed")
        if i < len(edits):
            tr.ev(15, K.FILE_FOCUS, "src/cart.py")
            tr.ev(25, K.EDIT, "src/cart.py", text=edits[i])
            tr.ev(4, K.SAVE, "src/cart.py")
    return tr


def exploring() -> Trace:
    tr = Trace("exploring", T0 + 20_000_000, session_elapsed_ms=3 * 60_000).episode("vendor/router/core.py")
    for p in ["README.md", "vendor/router/__init__.py", "vendor/router/core.py",
              "vendor/router/matchers.py"]:
        tr.ev(15, K.FILE_OPEN, p)
        tr.ev(20, K.CURSOR_JUMP, p, text="scroll")
    tr.ev(40, K.FILE_FOCUS, "vendor/router/core.py")
    tr.ev(30, K.CURSOR_JUMP, "vendor/router/core.py", text="line 40 -> 212 (def dispatch)")
    tr.ev(55, K.IDLE_START)
    tr.cand([(S.LONG_IDLE, 0.5, "no edits for 4 min", 1, 240),
             (S.FILE_THRASH, 0.6, "core.py revisited 3x", 3, 150)],
            False, "Reading unfamiliar code; idle is reading time.")
    tr.ev(70, K.IDLE_END)
    tr.ev(5, K.FILE_FOCUS, "vendor/router/matchers.py")
    tr.ev(25, K.CURSOR_JUMP, "vendor/router/matchers.py", text="line 1 -> 88 (class PathMatcher)")
    tr.ev(20, K.FILE_FOCUS, "vendor/router/core.py")
    tr.ev(15, K.TERMINAL_CMD, text="grep -rn 'register_route' vendor/", exit_code=0)
    tr.ev(20, K.FILE_OPEN, "vendor/router/registry.py")
    tr.ev(30, K.FILE_FOCUS, "vendor/router/core.py")
    tr.cand([(S.FILE_THRASH, 0.8, "core.py focused 5x in 6 min", 5, 360),
             (S.LONG_IDLE, 0.4, "no edits for 8 min", 1, 480)],
            False, "Tracing call graph deliberately (grep, new file each hop).")
    tr.ev(40, K.FILE_OPEN, "notes/router.md")
    tr.ev(30, K.EDIT, "notes/router.md", text="dispatch -> PathMatcher.match -> registry.lookup")
    tr.ev(90, K.IDLE_START)
    tr.cand([(S.LONG_IDLE, 0.6, "idle 90s after edit", 1, 90),
             (S.FILE_THRASH, 0.5, "core.py/matchers.py alternated 4x", 4, 500)],
            False, "Writing notes, then thinking.")
    return tr


def parser() -> Trace:
    tr = Trace("parser", T0 + 30_000_000, session_elapsed_ms=26 * 60_000).episode("src/parser.py")
    fp = "AttributeError:NoneType.children"
    err = ("AttributeError: 'NoneType' object has no attribute 'children'\n"
           "  File \"src/parser.py\", line 88, in _walk\n    for child in node.children:")
    tr.ev(0, K.FILE_OPEN, "src/parser.py")
    tr.ev(40, K.TEST_RUN, "tests/test_parser.py", exit_code=1, tests_passed=14, tests_failed=2,
          error_fingerprint=fp)
    tr.ev(50, K.EDIT, "src/parser.py", text="node = self._parse_block(tokens)")
    tr.ev(40, K.TEST_RUN, "tests/test_parser.py", exit_code=1, tests_passed=14, tests_failed=2,
          error_fingerprint=fp)
    tr.ev(50, K.EDIT, "src/parser.py", text="if node is None: continue")
    tr.ev(5, K.SAVE, "src/parser.py")
    tr.ev(35, K.TEST_RUN, "tests/test_parser.py", exit_code=1, tests_passed=14, tests_failed=2,
          error_fingerprint=fp)
    tr.wait(20)
    tr.cand([(S.REPEATED_ERROR, 0.8, "same AttributeError fingerprint 3x in 4 minutes", 3, 240),
             (S.TEST_LOOP, 0.6, "3 test runs, no change in pass count", 3, 200)],
            False, "Ambiguous: trying different fixes, only 3 attempts.",
            error=err, tests="14 passed, 2 failed: test_nested_block, test_empty_block")
    return tr


# ---------------------------------------------------------------------------
# stuck
# ---------------------------------------------------------------------------

def thrash() -> Trace:
    tr = Trace("thrash", T0 + 40_000_000, session_elapsed_ms=50 * 60_000).episode("app/handlers/session.py")
    fp = "KeyError:'user_id'"
    err = ("KeyError: 'user_id'\n  File \"app/handlers/session.py\", line 31, in load_session\n"
           "    uid = data['user_id']")
    # Line 31 never changes; they oscillate on how `data` is decoded (line 29)
    # while the payload actually nests the id under data['user']['id'].
    cycle = ["data = json.loads(raw)", "data = json.loads(raw.decode('utf-8'))",
             "data = json.loads(raw)", "data = dict(json.loads(raw))",
             "data = json.loads(raw)", "data = json.loads(raw.decode('utf-8'))"]
    tr.ev(0, K.FILE_OPEN, "app/handlers/session.py")
    for i, line in enumerate(cycle):
        tr.ev(25, K.EDIT, "app/handlers/session.py", text=line)
        tr.ev(4, K.SAVE, "app/handlers/session.py")
        tr.ev(10, K.TEST_RUN, "tests/test_session.py", exit_code=1, tests_passed=11,
              tests_failed=1, error_fingerprint=fp)
        n = i + 1
        if n == 2:
            tr.cand([(S.REPEATED_ERROR, 0.5, "KeyError 'user_id' 2x", 2, 80)],
                    False, "Only two attempts; normal debugging.", error=err,
                    tests="11 passed, 1 failed: test_load_session")
        if n == 4:
            tr.cand([(S.REPEATED_ERROR, 0.8, "KeyError 'user_id' 4x in 3 min", 4, 170),
                     (S.TEST_LOOP, 0.7, "4 runs, 1 failing every time", 4, 170),
                     (S.REVERT_CHURN, 0.7, "line 29 edited back to its original text twice", 2, 120)],
                    True, "Oscillating on decoding; never checks what the dict holds.",
                    error=err, tests="11 passed, 1 failed: test_load_session")
    tr.ev(10, K.FILE_FOCUS, "tests/test_session.py")
    tr.ev(8, K.FILE_FOCUS, "app/handlers/session.py")
    tr.ev(6, K.FILE_FOCUS, "tests/test_session.py")
    tr.ev(7, K.FILE_FOCUS, "app/handlers/session.py")
    tr.wait(50)
    tr.cand([(S.REPEATED_ERROR, 0.95, "KeyError 'user_id' 6x in 5 min", 6, 300),
             (S.TEST_LOOP, 0.9, "6 runs, identical result", 6, 300),
             (S.REVERT_CHURN, 0.85, "line 29 reverted 3x", 3, 240),
             (S.FILE_THRASH, 0.6, "session.py <-> test_session.py 4x in 30s", 4, 30),
             (S.STALLED_WITH_ERROR, 0.6, "50s idle with KeyError unresolved", 1, 50)],
            True, "Clearly stuck and paused: best moment to speak.",
            error=err, tests="11 passed, 1 failed: test_load_session")
    return tr


def blocked() -> Trace:
    tr = Trace("blocked", T0 + 50_000_000, session_elapsed_ms=35 * 60_000).episode("app/config.py")
    fp = "ImportError:circular:app.config"
    err = ("ImportError: cannot import name 'Settings' from partially initialized module "
           "'app.config' (most likely due to a circular import)\n"
           "  File \"app/config.py\", line 3, in <module>\n    from app.db import engine\n"
           "  File \"app/db.py\", line 2, in <module>\n    from app.config import Settings")
    tr.ev(0, K.FILE_OPEN, "app/config.py")
    tr.ev(20, K.TERMINAL_CMD, text="python -m app", exit_code=1, error_fingerprint=fp)
    tr.ev(30, K.EDIT, "app/config.py", text="from app.db import engine  # moved to top")
    tr.ev(8, K.TERMINAL_CMD, text="python -m app", exit_code=1, error_fingerprint=fp)
    tr.cand([(S.REPEATED_ERROR, 0.5, "ImportError circular 2x", 2, 40)],
            False, "Second attempt, still mid-flow.", error=err)
    tr.ev(25, K.FILE_OPEN, "app/db.py")
    tr.ev(30, K.EDIT, "app/db.py", text="import app.config as config")
    tr.ev(8, K.TERMINAL_CMD, text="python -m app", exit_code=1, error_fingerprint=fp)
    tr.ev(20, K.EDIT, "app/db.py", text="from app.config import Settings")
    tr.ev(8, K.TERMINAL_CMD, text="python -m app", exit_code=1, error_fingerprint=fp)
    tr.ev(15, K.FILE_FOCUS, "app/config.py")
    tr.ev(20, K.EDIT, "app/config.py", text="from app.db import engine")
    tr.ev(8, K.TERMINAL_CMD, text="python -m app", exit_code=1, error_fingerprint=fp)
    tr.ev(5, K.IDLE_START)
    tr.wait(110)
    tr.cand([(S.REPEATED_ERROR, 0.9, "same circular ImportError 4x in 4 min", 4, 240),
             (S.STALLED_WITH_ERROR, 0.9, "115s idle, ImportError unresolved", 1, 115),
             (S.REVERT_CHURN, 0.6, "app/db.py import changed then restored", 2, 90),
             (S.LONG_IDLE, 0.5, "no activity for ~2 min", 1, 115)],
            True, "Stopped on a circular import they keep reshuffling; config<->db cycle is the fix.",
            error=err)
    return tr


# ---------------------------------------------------------------------------
# budget stress
# ---------------------------------------------------------------------------

def budget30() -> Trace:
    """A bad 18-minute stretch: the heuristics fire 30 times. Most moments
    look stuck-ish; some really are. Short enough that the bucket refills
    less than one token (3/hour => one per 20 min)."""
    tr = Trace("budget30", T0 + 60_000_000, session_elapsed_ms=70 * 60_000)
    bugs = [
        ("app/handlers/session.py", "KeyError:'user_id'", "KeyError: 'user_id'\n  File \"app/handlers/session.py\", line 31",
         ["uid = data['user_id']", "uid = data.get('user_id')", "uid = data['user_id']"]),
        ("app/tasks/retry.py", "TimeoutError:redis", "TimeoutError: Timed out connecting to redis:6379\n  File \"app/tasks/retry.py\", line 57",
         ["timeout=5", "timeout=10", "timeout=5"]),
        ("app/api/upload.py", "ValueError:boundary", "ValueError: Invalid boundary in multipart: None\n  File \"app/api/upload.py\", line 19",
         ["parse(request.body)", "parse(request.stream)", "parse(request.body)"]),
    ]
    n = 0
    for b, (path, fp, err, edits) in enumerate(bugs):
        tr.episode(path)
        tr.ev(0, K.FILE_OPEN, path)
        for k in range(10):
            tr.ev(12, K.EDIT, path, text=edits[k % 3])
            tr.ev(8, K.TEST_RUN, "tests/", exit_code=1, tests_passed=40, tests_failed=1,
                  error_fingerprint=fp)
            reps = k + 1
            sigs = [(S.REPEATED_ERROR, min(1.0, 0.3 + 0.07 * reps), f"{fp} {reps}x", reps, 20 * reps),
                    (S.TEST_LOOP, min(1.0, 0.25 + 0.07 * reps), f"{reps} runs, 1 failing", reps, 20 * reps)]
            if reps >= 3:
                sigs.append((S.REVERT_CHURN, 0.7, f"{path} edit reverted {reps // 3}x", reps // 3, 40))
            tr.cand(sigs, reps >= 4, "stuck from ~4th identical run", error=err,
                    tests="40 passed, 1 failed")
            n += 1
    assert n == 30
    return tr


class _Materialized(Trace):
    """A trace whose candidates come from Person 1's capture engine replaying a
    real-format raw editor trace, rather than from this file's builder."""

    def __init__(self, name: str, raw: str, labels: list[tuple[bool, str]]) -> None:
        super().__init__(name, 0)
        import extention
        with open(TRACE_DIR / raw, "r", encoding="utf-8") as fh:
            self.cands = extention.replay(extention.Engine(extention.Redactor()), fh)
        for i, c in enumerate(self.cands):     # deterministic ids for stable hashes
            c.candidate_id, c.snapshot.snapshot_id = f"cand_{name}_{i:02d}", f"snap_{name}_{i:02d}"
            c.snapshot.episode.episode_id = f"ep_{name}01"
            for e in c.snapshot.recent_events:
                e.episode_id = f"ep_{name}01"
        assert len(self.cands) == len(labels), (name, len(self.cands))
        self.labels = [LabeledMoment(c.ts, lab, why, tolerance_ms=1000)
                       for c, (lab, why) in zip(self.cands, labels)]


def p1_stuck() -> Trace:
    """Person 1's stuck.raw.jsonl: real editor event shapes (edits carry sizes,
    never text). Labels are mine, from the trace's own description."""
    return _Materialized("p1_stuck", "stuck.raw.jsonl", [
        (False, "3 identical failures: still ordinary debugging."),
        (True, "4 identical failures, identical +3/-3 tweaks, then stalled."),
    ])


SCENARIOS = [refactor, tdd, exploring, parser, thrash, blocked, budget30, p1_stuck]
CALIBRATION = ["refactor", "tdd", "exploring", "parser", "thrash", "blocked", "p1_stuck"]
PRODUCTIVE = {"refactor", "tdd", "exploring", "parser"}

if __name__ == "__main__":
    for f in SCENARIOS:
        tr = f()
        tr.write()
        span = (tr.cands[-1].ts - tr.cands[0].ts) / 60000 if tr.cands else 0
        print(f"{tr.name:<10} {len(tr.cands):>2} candidates, "
              f"{sum(l.should_have_spoken for l in tr.labels)} labeled speak, span {span:.1f} min")
