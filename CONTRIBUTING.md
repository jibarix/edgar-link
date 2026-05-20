# Contributing

`edgar-link` is a personal public project.

**Contribution policy:** No direct collaborator access; all external
contributions must come through pull requests from forks. There are no
maintainers other than the repository owner, and merge decisions are at
the owner's discretion.

## How to contribute

1. Fork the repository.
2. Create a branch off `main` in your fork.
3. Make focused, single-purpose changes. Keep the diff minimal and
   self-contained — large refactors or speculative abstractions are
   unlikely to land.
4. Open a pull request against `jibarix/edgar-link:main`.

## What to expect on a PR

- CI (`Offline tests + MCP boot (Windows / Python 3.11)`) must pass.
- At least one approving review is required before merge.
- All review conversations must be resolved before merge.
- Force-pushes and branch deletions on `main` are blocked.
- `main` is required to be up to date with the base before merge.

## Local development

See [`README.md`](./README.md) for install, dev setup, and validation
entry points (`scripts/smoke_test_metrics.py`, `python main.py`,
`python -m edgar_mcp`).

`EDGAR_IDENTITY` (or the legacy alias `SEC_EDGAR_USER_AGENT`) is
required for any live SEC retrieval. Never hardcode, borrow, or commit
someone else's identity.

## Scope

- Bug fixes against the parser, metric registry, MCP server, or CLI
  are welcome.
- Changes that affect metric semantics or the closed-set BS/CF
  taxonomy need a clear analyst rationale in the PR description.
- Dependency bumps need to explain why the version was chosen and
  include the audit rationale in the PR description. Lockfile changes
  should be regenerated with `scripts/gen_lockfile.py`.

## Security reports

Do not open public issues or PRs for security vulnerabilities. Use the
process in [`SECURITY.md`](./SECURITY.md) instead.
