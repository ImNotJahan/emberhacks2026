"""
p3_import.py — forward the judge's decision logs (logs/<session>.jsonl) to
the P3 server, so replays and live runs show on the dashboard without any
change to the judge's code.

    python p3_import.py                              # every judge log in logs/, once
    python p3_import.py logs/thrash__judge-v6.jsonl  # just these files
    python p3_import.py --follow                     # keep forwarding new lines (live judge)

The server ignores records it already has, so running this twice is safe.
"""

import argparse
import sys
import time
from pathlib import Path
from urllib.error import HTTPError

from p3_send import _call

LOG_DIR = Path(__file__).resolve().parent / "logs"
OWN_FILES = {"mailbox.jsonl"}   # the server's own save file; never forward it


def judge_logs() -> list:
    return sorted(p for p in LOG_DIR.glob("*.jsonl") if p.name not in OWN_FILES)


def forward(path: Path, offset: int = 0) -> tuple:
    """Send every complete line after `offset`. Returns (new offset, counts).
    Stops early if the server is unreachable, so nothing is skipped over."""
    counts = {"new": 0, "evidence added": 0, "already there": 0, "skipped": 0}
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                break                      # the judge is mid-write; pick it up next pass
            line = raw.strip()
            if line:
                try:
                    resp = _call("/log", line.decode("utf-8"))
                    outcome = ("skipped" if not resp.get("ok") else "already there" if resp.get("duplicate")
                               else "evidence added" if resp.get("upgraded") else "new")
                except HTTPError:
                    outcome = "skipped"    # server rejected this line; move on
                except OSError:
                    break                  # server down: retry this line next pass
                except ValueError:
                    outcome = "skipped"
                counts[outcome] += 1
            offset += len(raw)
    return offset, counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--follow", action="store_true", help="keep watching logs/ for new lines")
    ap.add_argument("--every", type=float, default=1.0, help="seconds between passes with --follow")
    a = ap.parse_args()

    offsets = {}
    while True:
        for path in a.files or judge_logs():
            start = offsets.get(path, 0)
            if path.stat().st_size < start:
                start = 0                      # file was replaced (a fresh replay)
            offsets[path], counts = forward(path, start)
            if any(counts.values()):
                print(f"{path.name}: " + ", ".join(f"{n} {k}" for k, n in counts.items() if n), flush=True)
        if not a.follow:
            break
        time.sleep(a.every)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
