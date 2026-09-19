"""
test_capture.py — checks the capture engine against synthetic recorded sessions.

    python test_capture.py                 # run the tests
    python test_capture.py --write-traces  # dump traces/*.raw.jsonl for replay demos
"""

from __future__ import annotations

import json
import os
import sys
import tracemalloc
import unittest

from contract import EpisodeEnd, EventKind, SignalKind, to_json
from extention import Engine, Redactor, RingBuffer, replay

T0 = 1_800_000_000_000
PARSER_ERR = "FAILED tests/test_parser.py\nAttributeError: 'NoneType' object has no attribute 'children'\n2 failed, 3 passed in 0.4s"


def ev(t_s: float, k: str, **kw) -> dict:
    return {"k": k, "ts": T0 + int(t_s * 1000), **kw}


def test_run(t_s, out, code, is_test=True):
    return ev(t_s, "terminal_out", text=out, exit_code=code,
              meta={"cmd": "pytest", "is_test": is_test})


def stuck_trace() -> list[dict]:
    """Same AttributeError over and over, tiny edits, then a long stall."""
    t = [ev(0, "file_open", path="src/parser.py", lang="python"),
         ev(5, "file_focus", path="tests/test_parser.py", lang="python"),
         ev(8, "file_focus", path="src/parser.py", lang="python")]
    for i, at in enumerate((12, 50, 90, 130)):
        t.append(ev(at - 8, "edit", path="src/parser.py", lang="python", meta={"added": 3, "removed": 3}))
        t.append(test_run(at, PARSER_ERR, 1))
    t.append(ev(135, "diagnostic", path="src/parser.py", lang="python",
                text="Cannot access attribute 'children' for class 'None'", meta={"count": 1}))
    t.append(ev(140, "file_focus", path="tests/test_parser.py", lang="python"))
    t.append(ev(200, "file_focus", path="src/parser.py", lang="python"))   # then nothing
    return t


def productive_trace() -> list[dict]:
    """Changing errors, rising pass counts, fail->pass, commit. Should stay quiet."""
    t = [ev(0, "file_open", path="src/api.py", lang="python")]
    errors = [
        ("NameError: name 'fetch' is not defined\n1 failed, 4 passed", 1),
        ("TypeError: fetch() missing 1 required positional argument: 'url'\n1 failed, 4 passed", 1),
        ("AssertionError: expected 200 got 404\n1 failed, 6 passed", 1),
    ]
    at = 10
    for out, code in errors:
        for j in range(6):
            t.append(ev(at + j * 2, "edit", path="src/api.py", lang="python", meta={"added": 20, "removed": 2}))
        t.append(ev(at + 14, "save", path="src/api.py", lang="python"))
        t.append(test_run(at + 16, out, code))
        at += 40
    t.append(ev(at, "edit", path="src/api.py", lang="python", meta={"added": 12, "removed": 1}))
    t.append(test_run(at + 5, "7 passed in 0.3s", 0))                    # fail -> pass: boundary
    t.append(ev(at + 20, "terminal_out", text="[main abc123] add api", exit_code=0,
                meta={"cmd": "git commit -m add api", "is_test": False}))   # commit: boundary
    t.append(ev(at + 40, "file_open", path="src/models.py", lang="python"))
    for j in range(5):
        t.append(ev(at + 45 + j * 3, "edit", path="src/models.py", lang="python", meta={"added": 15, "removed": 0}))
    return t


def run(trace: list[dict], redact=True) -> tuple[Engine, list]:
    eng = Engine(Redactor(enabled=redact))
    cands = replay(eng, (json.dumps(r) for r in trace))
    return eng, cands


class Capture(unittest.TestCase):
    def test_stuck_session_fires(self):
        _, cands = run(stuck_trace())
        kinds = {s.kind for c in cands for s in c.signals}
        self.assertGreaterEqual(len(cands), 1)
        self.assertIn(SignalKind.REPEATED_ERROR, kinds)
        self.assertIn(SignalKind.TEST_LOOP, kinds)
        self.assertIn(SignalKind.STALLED_WITH_ERROR, kinds)

    def test_productive_session_stays_quiet(self):
        _, cands = run(productive_trace())
        self.assertEqual(cands, [], [s.detail for c in cands for s in c.signals])

    def test_candidates_are_debounced(self):
        _, cands = run(stuck_trace())
        gaps = [b.ts - a.ts for a, b in zip(cands, cands[1:])]
        self.assertTrue(all(g >= 60_000 for g in gaps))

    def test_episodes_split_where_a_human_would(self):
        eng, _ = run(productive_trace())
        ended = [e.ended_by for e in eng.seg.closed]
        self.assertIn(EpisodeEnd.TEST_PASSED, ended)
        self.assertIn(EpisodeEnd.COMMIT, ended)
        events = eng.buf.events()
        self.assertTrue(all(e.episode_id for e in events))       # every event tagged

    def test_idle_splits_episode(self):
        eng, _ = run(stuck_trace() + [ev(400, "edit", path="src/parser.py", lang="python")])
        self.assertIn(EpisodeEnd.IDLE, [e.ended_by for e in eng.seg.closed])
        kinds = [e.kind for e in eng.buf.events()]
        self.assertIn(EventKind.IDLE_START, kinds)
        self.assertIn(EventKind.IDLE_END, kinds)

    def test_replay_output_is_contract_json(self):
        _, cands = run(stuck_trace())
        for c in cands:
            d = json.loads(to_json(c))
            self.assertEqual(set(d), {"candidate_id", "ts", "snapshot", "signals", "budget"})
            self.assertLessEqual(len(d["snapshot"]["recent_events"]), 80)

    def test_ring_buffer_bounded_by_count_and_time(self):
        rb = RingBuffer(window_ms=300_000, max_events=2000)
        from contract import Event
        for i in range(50_000):                                   # burst: 50k events in 5s
            rb.append(Event(EventKind.EDIT, ts=T0 + i // 10, path="a.py"))
        self.assertLessEqual(len(rb), 2000)
        rb.append(Event(EventKind.EDIT, ts=T0 + 10 * 60_000, path="a.py"))
        self.assertEqual(len(rb), 1)                              # time pruning

    def test_twenty_minute_soak_no_memory_growth(self):
        eng = Engine(Redactor())
        def feed(start_s, end_s):
            for s in range(start_s, end_s):
                eng.ingest(ev(s, "edit", path=f"f{s % 7}.py", lang="python", meta={"added": 1, "removed": 0}))
                if s % 3 == 0:
                    eng.ingest(ev(s + 0.5, "file_focus", path=f"f{s % 5}.py", lang="python"))
                eng.tick(T0 + s * 1000)
        tracemalloc.start()
        feed(0, 5 * 60)                                           # warm up past the window
        warm, _ = tracemalloc.get_traced_memory()
        feed(5 * 60, 25 * 60)                                     # 20 more minutes
        end, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLessEqual(len(eng.buf), 2000)
        self.assertLess(end - warm, 500_000, f"grew {end - warm} bytes over 20 minutes")


class Redaction(unittest.TestCase):
    def setUp(self):
        self.r = Redactor()

    def test_strips_secrets(self):
        cases = {
            'api_key = "sk-abc123def456ghi789jkl"': "sk-abc",
            "export DB_PASSWORD=hunter2": "hunter2",
            'headers = {"Authorization": "Bearer abcdefghijklmnop"}': "abcdefghijklmnop",
            'url = "postgres://bob:s3cret@host/db"': "s3cret",
            'token: "ghp_abcdefghijklmnopqrstuvwxyz0123456789"': "ghp_abc",
        }
        for line, secret in cases.items():
            self.assertNotIn(secret, self.r.redact(line), line)

    def test_keeps_ordinary_code(self):
        for line in ['total = 5 + 3', 'raise ValueError("bad token count")', 'x = "hello world"']:
            self.assertEqual(self.r.redact(line), line)

    def test_env_file_contents_never_pass(self):
        self.assertEqual(self.r.redact("A=1\nB=2", ".env"), "[REDACTED:.env contents]")
        self.assertEqual(self.r.redact("A=1", "config/prod.env"), "[REDACTED:.env contents]")

    def test_toggle_off_passes_through(self):
        self.assertEqual(Redactor(enabled=False).redact('password = "x1y2z3"'), 'password = "x1y2z3"')

    def test_secrets_never_reach_events_or_candidates(self):
        trace = stuck_trace() + [ev(1, "terminal_out", text='key: "sk-abc123def456ghi789jkl"', exit_code=1,
                                    meta={"cmd": "TOKEN=abcdef123456 python x.py", "is_test": False})]
        eng, cands = run(sorted(trace, key=lambda r: r["ts"]))
        blob = "".join(to_json(e) for e in eng.buf.events()) + "".join(to_json(c) for c in cands)
        self.assertNotIn("sk-abc123", blob)
        self.assertNotIn("abcdef123456", blob)


if __name__ == "__main__":
    if "--write-traces" in sys.argv:
        os.makedirs("traces", exist_ok=True)
        for name, fn in (("stuck", stuck_trace), ("productive", productive_trace)):
            with open(f"traces/{name}.raw.jsonl", "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(r) + "\n" for r in fn())
        print("wrote traces/stuck.raw.jsonl, traces/productive.raw.jsonl")
    else:
        unittest.main()
