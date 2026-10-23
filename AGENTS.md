# Agent guidelines

## Commits

- Do not add a `Co-Authored-By: Claude …` trailer (or any AI co-author line) to commit messages or PR bodies. Commits are authored by the repo owner only.

## Agent skills

### Issue tracker

GitHub Issues on `ccomkhj/product-demo` via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Engineering Principle

- DRY (Don't Repeat Yourself)
- KISS (Keep It Stupid and Simple)