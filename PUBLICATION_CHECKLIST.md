# Publication Checklist

Use this before sending the project to another tester or publishing a public
repository.

Current public backend scope: `OpenViking + EchoMemory`.
Compatibility wording for docs and handoff: `OpenViking + EchoMem/EchoMemory`.

## Required Gates

```bash
./preflight.sh
curl -s <WEB_BASE_URL>/api/handoff-audit | python3 -m json.tool | head -160
curl -s <WEB_BASE_URL>/api/github-launch-kit | python3 -m json.tool | head -160
```

All required failures must be zero.

## Include

- source code
- `README.md`
- `README_ECHOMEM_LOCOMO_HANDOFF.md`
- `HARNESS_SPEC.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `LICENSE`
- `.github/pull_request_template.md`
- `.github/workflows/preflight.yml`
- `.gitignore`
- `.gitattributes`
- `env.echomem.example`
- `.github/ISSUE_TEMPLATE/`
- `web/ui_contract.json`
- core static UI files under `web/static` and `static`: `index.html`, `app.js`, `styles.css`, and `product-roadmap.html`
- `dataset/locomo10.json`
- bundled sample benchmark files such as `dataset/longmemeval.sample.json` and `dataset/evolvingevents.sample.json` when they are part of the intended public package

## Do Not Include

- `.env`, `.env.local`, `judge.conf`, or real API keys
- `runs/`, `dist/`, `outputs/`, `reports/`, or old generated packages
- OpenViking or EchoMem workspaces
- `external/` source checkouts
- `dataset/full/`
- historical static reports other than `index.html` and `product-roadmap.html`
- screenshots with tokens, private paths, or private model outputs

## Public README Must Show

- what the workbench does
- current scope: OpenViking + EchoMemory
- compatibility wording: OpenViking + EchoMem/EchoMemory
- public UI boundary from `web/ui_contract.json`
- 5-minute smoke test
- safe configuration template
- EchoMem fork integration contract
- report artifacts and what they prove
- security and handoff warnings

## Demo Report Rule

Share only redacted reports. A good demo report should show evidence, context,
Judge status, token usage, run configuration, and failure attribution without
including private datasets, API keys, provider credentials, or full workspaces.
