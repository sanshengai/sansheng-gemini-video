# Programmatic use + the `reverse` output contract

This skill is a **perception layer**: give it a video, get back structured JSON. It does
**not** orchestrate, download, or produce anything. This page is for calling it from your
own code (a workflow, another skill, a batch job) instead of the CLI.

---

## Two ways to call it

### A. Import `analyze_one` (same process)

```python
import sys
from pathlib import Path

# point at this skill's scripts dir
sys.path.insert(0, str(Path.home() / ".claude" / "skills" / "sansheng-gemini-video" / "scripts"))
from analyze_video import analyze_one

result = analyze_one(
    source,                 # local absolute path OR a YouTube URL
    intent="reverse",       # understand / screening / reverse
    start=None, end=None,   # trim to a segment to save tokens (do this for long clips)
    fps=None,               # 0.2-0.5 for screen recordings; 1.0 for talking-head
    media_resolution="low", # bump to "high" only to read small on-screen text
    auto_compress=True,     # ffmpeg-compress local files above the inline size cap
)
# result["analysis"] = the structured result;  result["usage"] = tokens / cost / seconds
```

`analyze_one` is idempotent and side-effect-free (except a compression temp file it deletes
in a `finally`). It raises `ValueError` (bad input) / `RuntimeError` (API / network / missing
key) so callers can `try` and continue.

### B. Write a JSON file (cross-process / cross-agent)

```bash
python scripts/analyze_video.py "<source>" --intent reverse --fps 0.5 --out analysis/gemini_01.json
```

Your downstream step just `Read`s the JSON. **For high-token video analysis, isolate it in a
sub-agent**: run the analysis inside the agent and return only a summary -- don't push the
raw base64 / long JSON back into the main context.

---

## Custom analysis: you define the schema

Don't fork the script to add your own fields. Drive it with `--prompt` + `--schema` (or
`custom_prompt` + `custom_schema` on `analyze_one`): **you** define the `responseSchema`, the
skill just runs Gemini against it and returns validated JSON. This keeps the perception layer
generic -- *what* to analyze is the caller's decision; *seeing* is the skill's.

---

## The `reverse` output contract

`intent="reverse"` reverse-engineers a video creator's **visual** and **audio** craft (for
study / inspiration). It deliberately does **not** touch soft dimensions (topic choice, script,
narrative) -- feed those from a subtitle/transcript analysis of your own. Output `analysis`:

```jsonc
{
  "summary": "...",
  "visualCraft": {
    "shotTimeline": [
      {"timecode": "00:03", "shot": "what's on screen (speech ↔ visual)", "technique": "transition / motion / lower-third / split / zoom / B-roll / live", "on_screen_text": "..."}
    ],
    "colorGrading": "palette / grade",
    "rhythm": "fast-cut / breathing room / beat-synced",
    "signatureMoves": ["signature visual habits"]
  },
  "audioCraft": {
    "bgm": "music style / mood / present-or-not",
    "sfx": [{"timecode": "00:05", "desc": "sound effect"}],
    "voiceProsody": "pace / pauses / stress",
    "syncPoints": [{"timecode": "00:08", "what": "beat / stress-peak hit"}]
  },
  "crossDimSignals": [
    {"timecode": "00:03", "contentSignal": "creating contrast", "visualTactic": "fast zoom + red box", "audioTactic": "drum hit"}
  ]
}
```

Field order puts observations (`shot`, `evidence`) before judgments (`technique`, `verdict`)
on purpose: Gemini generates in field order, so it records what it sees before it rules on it.

---

## `screening` / `understand` for footage triage

- `batch_eval.py <folder>` writes `_summary.json` with `usable` + `overall_score` +
  `suggested_action` per clip -- hand it to Claude to pick which clips to keep / re-shoot.
- `understand`'s `timeline` (timecode + visual + on_screen_text) gives you shot-level
  positions for editing.

---

## Cost & isolation notes

- `reverse` / `understand` on a full clip is not cheap -- for anything over ~2 min, pass
  `--start/--end` and a low `--fps`.
- Calling in bulk: go serial or low-concurrency on one key to avoid 429 rate limits.
- Isolate single high-token calls in a sub-agent; keep the main context on summaries only.
