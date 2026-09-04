# Failure Categories

| Category | Required evidence | EchoMem change | Allowed response |
|---|---|---|---|
| Git merge | PR state, base ref, head/base SHA, mergeable state | No | Report closed/non-develop/conflict and stop |
| Evaluation platform | Web traceback, Docker API argument error, path/permission error | No | Fix platform, restart Web, retry |
| Dependency | Docker build log, missing module/package, changed dependency fingerprint | Usually no | Rebuild dependency image once, retry |
| Model service | HTTP 401/403/429/5xx, timeout, empty model response | No | Check endpoint/key/rate limit, retry |
| Docker/server | inspect state, exit code, OOMKilled, disk/memory/port evidence | No | Restore server/Docker, retry |
| EchoMem config | checkout config parsing/validation error before service health | No source edit | Report incompatible config or platform override |
| EchoMem code | EchoMem traceback from the tested checkout with valid runtime config | Yes or PR follow-up | Report exact traceback and stop |

Do not infer an EchoMem defect when Docker logs or inspect evidence is absent. Do not
classify Judge failures as empty retrieval. Empty retrieval requires retrieval trace or
summary evidence such as `empty_retrieval_count > 0`.
