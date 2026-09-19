#!/usr/bin/env bash
# Move the capture engine's output out of the repo root after a recording.
#   recording_kit/save_session.sh          clear old output into traces/_old/ (before a session)
#   recording_kit/save_session.sh stuck1   save trace.raw.jsonl as traces/eval/stuck1.raw.jsonl
set -euo pipefail
cd "$(dirname "$0")/.."
stamp=$(date +%Y%m%d_%H%M%S)
old="traces/_old/$stamp"

if [ $# -eq 0 ]; then
  if ls trace.*.jsonl >/dev/null 2>&1; then mkdir -p "$old" && mv trace.*.jsonl "$old/" && echo "moved old engine output to $old/"
  else echo "nothing to clear"; fi
  exit 0
fi

name="$1"
dest="traces/eval/$name.raw.jsonl"
[ -f trace.raw.jsonl ] || { echo "no trace.raw.jsonl in $(pwd): is the engine running from this repo?" >&2; exit 1; }
[ -e "$dest" ] && { echo "$dest already exists" >&2; exit 1; }
mv trace.raw.jsonl "$dest"
mkdir -p "$old" && mv trace.*.jsonl "$old/" 2>/dev/null || true
python3 - "$dest" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
ts = [r["ts"] for r in rows]
sync = [r["ts"] for r in rows if r.get("k") == "terminal_cmd" and (r.get("text") or "").strip() == "echo SYNC"]
print(f"saved {sys.argv[1]}: {len(rows)} events, {(max(ts) - min(ts)) / 60000:.1f} min")
print(f"SYNC ts = {sync[0]}   (label ts = SYNC ts + (video_seconds - SYNC_video_seconds) * 1000)" if sync
      else "WARNING: no `echo SYNC` found; line labels up by another terminal command instead")
if (max(ts) - min(ts)) < 15 * 60000:
    print("WARNING: shorter than 15 minutes")
PY
echo "next: write traces/eval/$name.labels.jsonl, add \"$name\" to traces/eval/manifest.json"
