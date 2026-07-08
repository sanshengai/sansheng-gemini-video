# sansheng-gemini-video · Real eyes for video

> Hand a whole clip to Gemini's native multimodal model and get back a structured evaluation JSON.

[中文](./README.md) | **English**

## What it is

Claude can't watch video; it can only guess from a few sampled frames. This skill hands the
**whole clip** (picture + audio + timing) to Gemini and returns **machine-readable JSON**:
what happens, whether footage passes a rubric, or a breakdown of a creator's visual & audio craft.

It's a **perception layer** -- it doesn't download, edit, or publish; it just *sees*. What you do
with the seeing (selecting, editing, scripting) is your own workflow's job.

## What the output looks like -- see it before you install

It isn't a GUI tool; the output is JSON. Three intents, one core -- the **field structure of the samples below is taken from the real output contract** (see [`references/integration.md`](references/integration.md)); the values are illustrative:

### ① understand -- make sense of a clip

Returns an overall summary + a **timeline** (timecode / visual / on-screen text) + UI elements + issues. The timeline looks like this:

| Timecode | Visual | On-screen text |
|---|---|---|
| 00:00 | Host on camera, medium shot, blurred bookshelf behind | Title card: "X in 3 minutes" |
| 00:05 | Cut to screen recording, cursor opens a feature panel | "Step one" |
| 00:12 | Back to host, gesturing for emphasis, lower-third caption | "Here's the key part" |

### ② screening -- score footage against a rubric

Grades a clip against your rubric criterion by criterion: each gets **evidence + verdict + score**,
then rolls up `usable` / `overall_score` / `suggested_action`. `batch_eval.py` runs a whole folder
and hands you a selection table:

| File | Usable? | Score | Suggested action |
|---|---|---|---|
| clip_01.mp4 | ✅ | 8.5 | Keep -- steady shot, clean audio |
| clip_02.mp4 | ⚠️ | 5.0 | Usable but trim -- first 3s shaky |
| clip_03.mp4 | ❌ | 2.5 | Reshoot -- overexposed, dropped frames |

### ③ reverse -- reverse-engineer a creator's visual / audio craft

For study / inspiration, it breaks down `visualCraft` (shot timeline, color grading, rhythm,
signature moves) and `audioCraft` (BGM, SFX, prosody, sync points), plus a cross-dimension
"content-signal → tactic" mapping:

```jsonc
{
  "summary": "...",
  "visualCraft": {
    "shotTimeline": [
      {"timecode": "00:03", "shot": "what's on screen (speech ↔ visual)", "technique": "transition / motion / lower-third / split / zoom / B-roll", "on_screen_text": "..."}
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

> Field order deliberately puts observations (`shot`) before judgments (`technique`) -- Gemini
> generates in field order, so it records what it sees before ruling on what technique it is.

Every result carries a `usage` block (tokens / estimated cost / seconds). Keys are read from
env / `.env` only, never printed; `?key=` is redacted to `***` in any error text.

## When to use

Say *"analyze this video"*, *"分析这个录屏"*, *"can this clip be used?"*, *"batch-screen this
folder of clips"*, or drop a local video / a YouTube URL and ask what's in it -- Claude picks up
this skill and runs `analyze_video.py`.

**Not** for: downloading (`yt-dlp`), getting subtitles / a text summary (a subtitle tool), or
editing / producing a video.

## Install

As a Claude Code plugin (recommended):

```bash
claude plugin marketplace add sandypoli-boop/sansheng-gemini-video
claude plugin install sansheng-gemini-video
```

Or manually: clone and symlink into `~/.claude/skills/`:

```bash
git clone https://github.com/sandypoli-boop/sansheng-gemini-video.git
ln -s "$PWD/sansheng-gemini-video" ~/.claude/skills/sansheng-gemini-video
```

Then restart Claude Code.

## Quick start

```bash
pip install requests            # the only third-party dependency (raw REST, no SDK)
# ffmpeg is optional -- only to auto-compress large local videos
cp .env.example .env            # then set your key (see below)
python scripts/setup_check.py   # environment / key health check
python scripts/analyze_video.py "C:\path\to\clip.mp4"
```

## Which key do I need?

The scripts auto-detect your key by prefix -- no manual switching:

| Key | Prefix | Endpoint | Extra setup |
|-----|--------|----------|-------------|
| **AI Studio key** (most people) | `AIza…` | `generativelanguage.googleapis.com` | none -- just set the key |
| **Vertex AI Express key** | `AQ.…` | Vertex (`aiplatform.googleapis.com`) | also set `GEMINI_VERTEX_PROJECT` |

Set `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) in your environment or in `.env`. If the default
`gemini-3.5-flash` isn't available on your key, set `GEMINI_MODEL`. See [`.env.example`](.env.example).

> **Heads-up on the AI Studio path.** The Vertex Express (`AQ.`) path is battle-tested. The AI
> Studio (`AIza`) path is implemented and unit-checked (endpoint dispatch + key loading) but
> hasn't yet had a full end-to-end run against a real video. If you hit a problem with an AI
> Studio key, please [open an issue](../../issues) -- and try setting `GEMINI_MODEL` to a model
> your key can call.

## Cost control

Video tokens = frames (fps × duration) × per-frame tokens (by resolution). The biggest lever is
**fps** (0.2-0.5 for screen recordings); default resolution is `low` (bump to `high` only to read
small on-screen text). For long clips / YouTube, always pass `--start/--end` and a low `--fps`.
See [`references/visual-grounding-limits.md`](references/visual-grounding-limits.md).

## Programmatic use

`analyze_one()` is importable, idempotent, and side-effect-free. For high-token analyses, run it
inside a sub-agent and return only the summary. Interface + the `reverse` output contract are in
[`references/integration.md`](references/integration.md).

## Credits

The dependency health-check (`setup_check.py`, silent `--check` with graded exit codes) is
modeled on **[bradautomates/claude-video](https://github.com/bradautomates/claude-video)** (MIT).

This skill has a single third-party runtime dependency, `requests` (raw REST against Gemini, no
bundled SDK); `ffmpeg` is optional (compressing oversized video) and `yt-dlp` is only needed for
the YouTube path -- install them yourself; each keeps its own license. This repo ships under MIT
and bundles no third-party code.

## Article

A companion write-up is coming soon; link to follow.

## About the author · 关于作者

<p align="center">
  <a href="https://sanshengai.top"><strong>🌐 sanshengai.top</strong></a> ·
  <a href="https://namecard.xiaoyuzhoufm.com/nnl8x"><strong>🎧 Xiaoyuzhou (podcast)</strong></a> ·
  <a href="https://weibo.com/u/7546221967"><strong>Weibo</strong></a> ·
  <a href="https://www.xiaohongshu.com/user/profile/5c716b6d000000001000f5c4"><strong>Xiaohongshu</strong></a> ·
  <a href="mailto:sandypoli@gmail.com"><strong>✉️ Email</strong></a>
</p>

I'm **叁笙 (sansheng)** -- I use AI to make content and to build tools. This skill is what I ground out in real workflows while making「[叁笙早安 AI](https://sanshengai.top)」(*Sansheng Good Morning AI*), my personal site, then cleaned up and open-sourced. If it's useful, come look around the [site](https://sanshengai.top), or **scan to follow the WeChat account「叁笙早安AI」** (WeChat accounts have no click-through link, so scanning is quickest):

<p align="center">
  <img src="assets/qrcode-gongzhonghao.png" alt="WeChat official account 叁笙早安AI" width="200">
  <br><sub>Scan in WeChat to follow · 叁笙早安AI</sub>
</p>

## License

[MIT](LICENSE) © 2026 叁笙 (sansheng)
