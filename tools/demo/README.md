# Demo video

`make_video.sh` produces a ~3-minute narrated walkthrough of Home Inventory:

1. a fresh App on a throwaway data folder, filled with a **fictional** household (`seed.py`);
2. a scripted browser walkthrough recorded with Playwright (`record.mjs`), one scene per narration line;
3. a voiceover generated **offline** with [Piper](https://github.com/rhasspy/piper) from `narration.json`;
4. both mixed into an MP4 with ffmpeg.

```bash
pip install piper-tts
tools/demo/make_video.sh home-inventory-demo.mp4
```

Edit `narration.json` to change what's said; each scene lasts as long as its line. Set `DEMO_VOICE` to another Piper v0.0.2 voice (for example `voice-en-gb-alan-low`). Nothing leaves your machine except downloading the voice model once from GitHub.
