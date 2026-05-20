# Security Policy

## Reporting a Vulnerability

If you believe you've found a security vulnerability in `edgar-connect`,
please report it privately using **GitHub's Private Vulnerability
Reporting** for this repository:

→ https://github.com/jibarix/edgar-connect/security/advisories/new

Do **not** open a public issue, PR, or discussion for security reports.
A public report can give an attacker the same information you have
before a fix lands.

When reporting, include:

- A description of the vulnerability and its impact
- Steps to reproduce, or a minimal proof-of-concept
- The commit SHA or release you tested against
- Any suggested mitigation, if you have one

I'll acknowledge the report and follow up via the same advisory thread.
There is no SLA — this is a personal project with no paid support — but
I aim to triage within a few days.

## Supported Versions

Only the latest commit on `main` is supported. There is no LTS branch.
If a vulnerability is confirmed it will be fixed forward on `main`; a
new tagged release may follow at my discretion.

## Supply-Chain Posture

This repository pins runtime and development dependencies to exact
versions and uses a hash-pinned lockfile (`requirements.lock`). Please
do not open PRs that loosen pins, switch to unpinned ranges, or
introduce ad-hoc package execution (`pipx run`, `npx`, etc.) without a
written justification and matching audit notes in the PR description.

## Out of Scope

- Issues that depend on a compromised local environment (malware on the
  developer's machine, hijacked GitHub credentials, etc.)
- Throttling or rate-limiting by the SEC EDGAR APIs
- Behaviour of third-party MCP clients (Claude Code, Claude Desktop)
  beyond the documented tool contracts in `edgar_mcp/server.py`
