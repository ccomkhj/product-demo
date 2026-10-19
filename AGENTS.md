# Agent guidelines

## Commits

- Do not add a `Co-Authored-By: Claude …` trailer (or any AI co-author line) to commit messages or PR bodies. Commits are authored by the repo owner only.

## Agent skills

### Issue tracker

Issues and specs are local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels, written on a `Status:` line in each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
