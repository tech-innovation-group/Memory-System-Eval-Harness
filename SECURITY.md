# Security Policy

This is a local-first evaluation workbench. It can call external model
providers and can read/write local memory workspaces, so secret handling and
handoff boundaries matter.

## Supported Scope

Security reports should target the current public scope:

- OpenViking integration
- EchoMem/EchoMemory integration
- LoCoMo evaluation workflow
- report export, redaction, and handoff checks
- local Web UI and task APIs

## Reporting a Vulnerability

If the repository is hosted on GitHub, prefer GitHub private vulnerability
reporting. If that is not enabled, contact the maintainers privately before
opening a public issue.

Do not publish:

- real API keys or bearer tokens
- `.env.local`, `judge.conf`, or screenshots containing secrets
- raw `runs/`, private reports, or memory workspaces
- private dataset contents or private model outputs

## High-Impact Issues

Please report privately if you find:

- API keys exposed in UI, reports, logs, or exported packages
- workspace traversal or arbitrary file disclosure through report/path APIs
- cross-account leakage between memory workspaces
- generated handoff packages that include ignored local artifacts
- model-provider credentials transmitted to unexpected destinations

## Local Hardening

Before sharing or publishing a checkout:

```bash
./preflight.sh
curl -s <WEB_BASE_URL>/api/handoff-audit | python3 -m json.tool | head -160
```

The project intentionally ignores local secrets, run history, large datasets,
external source checkouts, and memory workspaces through `.gitignore` and
`.gitattributes`.
