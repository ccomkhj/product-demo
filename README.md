# product-demo

[![CI](https://github.com/ccomkhj/product-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/ccomkhj/product-demo/actions/workflows/ci.yml)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https://raw.githubusercontent.com/ccomkhj/product-demo/main/pyproject.toml)](pyproject.toml)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](https://claude.com/claude-code)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/github/license/ccomkhj/product-demo)](LICENSE)

**Ship the release. Skip the screen recording.**

## What is this?

A [Claude Code](https://claude.com/claude-code) plugin. You describe what you shipped in one
sentence; it gives you back a **narrated, subtitled demo video** of your web app.

https://github.com/user-attachments/assets/2c0ee19e-20e3-4949-8748-e35e23421094

*This 61 s video came from one line:*

```
/product-demo:run record a sample video for https://www.korea.net -> https://www.korea.net/AboutKorea/OverviewofKorea (explain only 3 stories about Korea) -> https://www.korea.net/Events/Festivals (2 Festival worth trying if you visit Korea). Use the presenter avatar assets/avatars/huijo.png.
```

## How it works

1. **Ask:** it asks for anything missing (pages, URLs) and writes a script.
2. **Approve:** you read the script and say yes.
3. **Record:** it drives a real Chrome browser, voices the script for free with local
   [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), and lands every click on the word
   that names it.
4. **Edit:** it cuts the dead time and burns in the subtitles.

## Install

You need `ffmpeg`, [`uv`](https://docs.astral.sh/uv/) and Node 20.19+ or 22.12+ (for the browser; older Node fails with `CONNECTION_CLOSED`).
Then, in Claude Code:

```
/plugin marketplace add ccomkhj/product-demo
/plugin install product-demo@product-demo
```

And in your terminal, add the browser:

```
claude mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest
```

Update later with `/plugin marketplace update product-demo`.

## Use it

```
/product-demo:run We shipped CSV export on the Reports page. Show a customer how to export last month's report.
```

You get three files in `demos/<slug>/`:

| File | What |
|------|------|
| `<slug>.mp4` | The video |
| `<slug>.srt` | The subtitles |
| `transcript.md` | The script |

## What else you can do

- **Post-process a finished video** with `/product-demo:postprocessing`. Ask in plain words:
  speed up, trim, title card, logo watermark, music, translated subtitles, chapters, GIF,
  size cap for Slack. The original stays untouched. ([guide](docs/postprocessing.md))

  ```
  /product-demo:postprocessing csv-export: make it 1.25x and add our logo logo.png top-right
  ```

- **Set your defaults once** with `/product-demo:run --configure`: voice (or record your
  own), presenter bubble, subtitles.
- **Go deeper:** voices, presenter bubble, preflight checks and internals are in the
  [guide](docs/guide.md).

## License

[Apache-2.0](LICENSE). Use, change, republish and sell it; keep the [`NOTICE`](NOTICE) file.
