## Summary

Describe the change and the memory-evaluation workflow it affects.

Current public backend scope: `OpenViking + EchoMemory`.

## Scope

- [ ] LoCoMo workflow
- [ ] OpenViking adapter
- [ ] EchoMem / EchoMemory adapter
- [ ] Report export or run analysis
- [ ] Web UI
- [ ] Documentation or handoff materials

## Safety Checklist

- [ ] I did not add `.env`, `.env.local`, `judge.conf`, API keys, bearer tokens, raw `runs/`, or memory workspaces.
- [ ] The current backend scope remains OpenViking + EchoMemory.
- [ ] If I changed `web/static`, I mirrored the core files to `static`.
- [ ] I ran `./preflight.sh` locally, or explained why it could not run.
- [ ] Any attached report, screenshot, or log is redacted.

## Verification

Paste the relevant safe output:

```text
./preflight.sh
```

## Notes

Mention any known follow-up work, model/provider dependency, or benchmark limitation.
