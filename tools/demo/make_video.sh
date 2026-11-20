#!/usr/bin/env bash
# Builds docs/demo video: a fresh App with fictional demo data, a recorded
# browser walkthrough and an offline (Piper) voiceover, mixed with ffmpeg.
#
#   tools/demo/make_video.sh [output.mp4]
#
# Needs: python3 with the App's dependencies, node + Playwright (Chromium),
# ffmpeg, and `pip install piper-tts`. Nothing is sent anywhere: the App runs
# locally on a throwaway data folder and the voice is generated on this machine.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/home-inventory-demo.mp4}"
WORK="$(mktemp -d)"
DATA="$WORK/data"
VOICE="${DEMO_VOICE:-voice-en-us-lessac-medium}"
mkdir -p "$DATA" "$WORK/audio"
trap 'kill $(cat "$WORK/pid" 2>/dev/null) 2>/dev/null || true' EXIT

echo "1/5 Voice"
if [ ! -d "$HOME/.cache/piper/$VOICE" ]; then
  mkdir -p "$HOME/.cache/piper/$VOICE"
  curl -sfL "https://github.com/rhasspy/piper/releases/download/v0.0.2/$VOICE.tar.gz" | tar -xz -C "$HOME/.cache/piper/$VOICE"
fi
MODEL="$(ls "$HOME/.cache/piper/$VOICE"/*.onnx | head -1)"
python3 - "$HERE/narration.json" "$WORK" "$MODEL" <<'PY'
import json, subprocess, sys, wave
narr, work, model = sys.argv[1:]
out = {}
for key, text in json.load(open(narr)):
    p = f"{work}/audio/{key}.wav"
    subprocess.run(["python3", "-m", "piper", "-m", model, "-f", p, "--sentence-silence", "0.35", "--length-scale", "1.05"],
                   input=text.encode(), check=True, capture_output=True)
    with wave.open(p) as w:
        out[key] = round(w.getnframes() / w.getframerate(), 2)
json.dump(out, open(f"{work}/durations.json", "w"))
PY

echo "2/5 Frontend and App"
(cd "$ROOT/home_inventory/frontend" && npm run build >/dev/null)
(cd "$ROOT/home_inventory/server" && HINV_DATA_DIR="$DATA" HINV_TRUSTED_PROXIES=127.0.0.1 HINV_ADMIN_USERS=alex \
  HINV_INGRESS_PORT=18099 HINV_API_PORT=18100 HINV_AI_PROVIDER=none HINV_CURRENCY=USD \
  python3 -m home_inventory_server >"$WORK/server.log" 2>&1 & echo $! >"$WORK/pid")
for _ in $(seq 1 30); do curl -sf localhost:18099/api/health >/dev/null && break; sleep 1; done

echo "3/5 Demo data"
python3 "$HERE/seed.py" "$WORK/ids.json"

echo "4/5 Recording"
cp "$HERE/record.mjs" "$ROOT/tests/e2e/.demo-record.mjs"   # resolves Playwright like the smoke test
node "$ROOT/tests/e2e/.demo-record.mjs" "$WORK"; rm -f "$ROOT/tests/e2e/.demo-record.mjs"

echo "5/5 Mixing"
python3 - "$WORK" "$OUT" <<'PY'
import json, subprocess, sys
work, out = sys.argv[1:]
video = open(f"{work}/video_path.txt").read().strip()
mk = json.load(open(f"{work}/marks.json")); marks, total = mk["marks"], mk["total"]
keys = list(marks)
inputs = ["-i", video] + sum((["-i", f"{work}/audio/{k}.wav"] for k in keys), [])
parts = [f"[{i}:a]aresample=48000,adelay={int((marks[k] + .35) * 1000)}|{int((marks[k] + .35) * 1000)},apad[a{i}]"
         for i, k in enumerate(keys, start=1)]
fc = (";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(1, len(keys) + 1))
      + f"amix=inputs={len(keys)}:normalize=0:duration=longest,atrim=0:{total:.2f},afade=t=out:st={total - 1.5:.2f}:d=1.5,"
      + f"loudnorm=I=-16:TP=-1.5[aout];[0:v]fade=t=in:st=0:d=0.8,fade=t=out:st={total - 1.2:.2f}:d=1.2,format=yuv420p[vout]")
subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-r", "30", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", "-t", f"{total:.2f}", out], check=True)
PY
echo "Done: $OUT"
