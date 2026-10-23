# product-demo

**Ship the release. Skip the screen recording.**

A [Claude Code](https://claude.com/claude-code) plugin that turns a sentence about what you
shipped into a **narrated, subtitled demo video**. It drives a real Chrome browser, voices
the script for free with local [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), puts
every click on the word that names it, and cuts the dead time.

https://github.com/user-attachments/assets/6b411193-4300-417b-8ec8-564527144894

*54 s, with a presenter bubble in the corner, made from this one line:*

```
/product-demo:run record a sample video for https://www.korea.net -> https://www.korea.net/AboutKorea/OverviewofKorea (explain only 3 bullet points about Korea) -> https://www.korea.net/Government/Administration/President-Lee-Jae-Myung (just tell who the president is and when he was elected). Use the presenter avatar assets/avatars/huijo.png.
```

## Install

In Claude Code:

```
/plugin marketplace add ccomkhj/product-demo
/plugin install product-demo@product-demo
```

Then add the browser MCP server:

```
claude mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest
```

Needs `ffmpeg` and [`uv`](https://docs.astral.sh/uv/).
The chrome-devtools MCP server needs the Node version in `chrome-devtools-mcp`'s `engines`
(`npm view chrome-devtools-mcp engines`, currently `^20.19 || ^22.12 || >=23`); older Node
fails with `CONNECTION_CLOSED`.

**Breaking change:** the skill is now `/product-demo:run` (and `/product-demo:run --configure`).
The old `/product-demo` command and the skills.sh install are gone; reinstall as a plugin.

## Use it

```
/product-demo:run We shipped CSV export on the Reports page. Show a customer how to export last month's report.
```

It asks what's missing, you approve the script, and you get `<slug>.mp4`, `<slug>.srt` and
`transcript.md` in `demos/<slug>/`.

To edit a finished demo without touching the original (speed, trims, cuts), ask
`/product-demo:postprocessing`, e.g. `/product-demo:postprocessing csv-export: 1.25x, cut the first 2 s`.
It writes `<slug>-post.mp4` and `<slug>-post.srt`.

Run `/product-demo:run --configure` once to set up voice (or record your own), presenter and subtitles with the agent.

Voices, your own voice, presenter bubble, preflight checks and internals: see the
[guide](docs/guide.md).

## License

[Apache-2.0](LICENSE). You may use, change, republish and sell it; copies and derivative
works must keep the [`NOTICE`](NOTICE) file, which credits this repository.
