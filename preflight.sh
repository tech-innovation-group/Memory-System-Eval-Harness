#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${LOCOMO_EVAL_HOST:-127.0.0.1}"
PORT="${LOCOMO_EVAL_PORT:-19181}"
BASE_URL="http://${HOST}:${PORT}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/locomo-preflight.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

ok=0
warn=0
fail=0

print_header() {
  printf "\n== %s ==\n" "$1"
}

pass() {
  printf "OK   %s\n" "$1"
  ok=$((ok + 1))
}

warn_msg() {
  printf "WARN %s\n" "$1"
  shift || true
  for line in "$@"; do
    [ -n "$line" ] && printf "     %s\n" "$line"
  done
  warn=$((warn + 1))
}

fail_msg() {
  printf "FAIL %s\n" "$1"
  shift || true
  for line in "$@"; do
    [ -n "$line" ] && printf "     %s\n" "$line"
  done
  fail=$((fail + 1))
}

check() {
  local name="$1"
  shift
  local out="$TMP_DIR/${name//[^A-Za-z0-9_.-]/_}.out"
  local err="$TMP_DIR/${name//[^A-Za-z0-9_.-]/_}.err"
  if "$@" >"$out" 2>"$err"; then
    pass "$name"
  else
    fail_msg "$name" "$(sed 's/^/    /' "$err" | head -5)"
  fi
}

check_warn() {
  local name="$1"
  shift
  local out="$TMP_DIR/${name//[^A-Za-z0-9_.-]/_}.out"
  local err="$TMP_DIR/${name//[^A-Za-z0-9_.-]/_}.err"
  if "$@" >"$out" 2>"$err"; then
    pass "$name"
  else
    warn_msg "$name" "$(sed 's/^/    /' "$err" | head -5)"
  fi
}

echo "LoCoMo Memory Eval preflight"
echo "Root: $ROOT"
echo "Web:  $BASE_URL"
echo "Scope: OpenViking + EchoMem/EchoMemory"

print_header "Runtime"
check "python3 available" command -v python3
check "start.sh syntax" bash -n "$ROOT/start.sh"
check "preflight.sh syntax" bash -n "$ROOT/preflight.sh"
if command -v node >/dev/null 2>&1; then
  check "web/static/app-state.js syntax" node --check "$ROOT/web/static/app-state.js"
  check "web/static/app-core.js syntax" node --check "$ROOT/web/static/app-core.js"
  check "web/static/app-format.js syntax" node --check "$ROOT/web/static/app-format.js"
  check "web/static/app.js syntax" node --check "$ROOT/web/static/app.js"
  check "static/app-state.js syntax" node --check "$ROOT/static/app-state.js"
  check "static/app-core.js syntax" node --check "$ROOT/static/app-core.js"
  check "static/app-format.js syntax" node --check "$ROOT/static/app-format.js"
  check "static/app.js syntax" node --check "$ROOT/static/app.js"
  check "frontend MemoryBench agent alignment gate" node "$ROOT/scripts/check_frontend_alignment.js"
else
  warn_msg "node not found" "Skipping frontend syntax checks."
fi

print_header "Python Syntax"
PY_FILES=(
  "$ROOT/server.py"
  "$ROOT/memory/adapters/__init__.py"
  "$ROOT/memory/adapters/base.py"
  "$ROOT/memory/adapters/contract.py"
  "$ROOT/memory/adapters/doctor.py"
  "$ROOT/memory/adapters/registry.py"
  "$ROOT/scripts/adapter_doctor.py"
  "$ROOT/scripts/echomemory_common.py"
  "$ROOT/scripts/echomemory_locomo_import.py"
  "$ROOT/scripts/echomemory_memory_qa.py"
  "$ROOT/scripts/openviking_locomo_import.py"
  "$ROOT/scripts/openviking_memory_qa.py"
  "$ROOT/scripts/local_judge.py"
  "$ROOT/scripts/generate_html_report.py"
)
check "core Python modules" python3 -m py_compile "${PY_FILES[@]}"

print_header "Adapter Doctor"
check "memory backend adapter contracts" python3 "$ROOT/scripts/adapter_doctor.py" --format text --strict

print_header "Backend Source Scope"
LOCOMO_PREFLIGHT_ROOT="$ROOT" python3 - <<'PY' >"$TMP_DIR/backend_scope.out" 2>"$TMP_DIR/backend_scope.err"
import json
import os
from pathlib import Path

root = Path(os.environ["LOCOMO_PREFLIGHT_ROOT"])
contract_path = root / "web/ui_contract.json"
contract = json.loads(contract_path.read_text(encoding="utf-8"))
allowed = {
    str(item.get("id") or "")
    for item in contract.get("memory_backends", [])
    if item.get("id")
}
if allowed != {"openviking", "echomemory"}:
    raise SystemExit(f"current release must expose exactly openviking and echomemory, got {sorted(allowed)}")
sidebar = contract.get("sidebar") or []
if len(sidebar) != 10:
    raise SystemExit(f"sidebar contract must contain 10 entries, got {len(sidebar)}")
if (contract.get("agent") or {}).get("label") != "MemoryBench Agent":
    raise SystemExit("agent label must be MemoryBench Agent")
delivery = contract.get("delivery_boundary") or {}
public_static = sorted(str(item) for item in delivery.get("public_static_files", []) if str(item).strip())
expected_public_static = sorted([
    "web/static/index.html",
    "web/static/app-state.js",
    "web/static/app-core.js",
    "web/static/app-format.js",
    "web/static/app.js",
    "web/static/styles.css",
    "web/static/product-roadmap.html",
])
if public_static != expected_public_static:
    raise SystemExit(f"public static contract must contain the split public UI assets, got {public_static}")
if "experiment history" not in str(delivery.get("historical_static_policy") or ""):
    raise SystemExit("historical static policy must state that extra web/static HTML files are experiment history")
problems = {}
for rel in ("memory/adapters", "memory/plugins"):
    path = root / rel
    if not path.exists():
        problems[rel] = ["directory missing"]
        continue
    actual = {
        item.name
        for item in path.iterdir()
        if item.is_dir() and not item.name.startswith("__")
    }
    extra = sorted(actual - allowed)
    missing = sorted(allowed - actual)
    if extra or missing:
        problems[rel] = [
            *(f"unexpected: {name}" for name in extra),
            *(f"missing: {name}" for name in missing),
        ]
if problems:
    raise SystemExit(problems)
print("ui contract and backend source directories are limited to openviking and echomemory")
PY
if [ $? -eq 0 ]; then
  pass "ui contract + backend source directories OpenViking + EchoMem only"
else
  fail_msg "backend source directories" "$(cat "$TMP_DIR/backend_scope.err")"
fi

print_header "Static Assets"
LOCOMO_PREFLIGHT_ROOT="$ROOT" python3 - <<'PY' >"$TMP_DIR/static_assets.out" 2>"$TMP_DIR/static_assets.err"
import filecmp
import json
import os
from pathlib import Path

root = Path(os.environ["LOCOMO_PREFLIGHT_ROOT"])
contract = json.loads((root / "web/ui_contract.json").read_text(encoding="utf-8"))
public_static = [
    str(item)
    for item in (contract.get("delivery_boundary") or {}).get("public_static_files", [])
    if str(item).startswith("web/static/")
]
missing = [rel for rel in public_static if not (root / rel).exists()]
drift = []
for rel in public_static:
    legacy = "static/" + rel.removeprefix("web/static/")
    if not (root / legacy).exists():
        missing.append(legacy)
        continue
    if not filecmp.cmp(root / rel, root / legacy, shallow=False):
        drift.append(f"{rel} != {legacy}")
if missing or drift:
    raise SystemExit({"missing": missing, "drift": drift})
print("public static contract mirrors web/static into static")
PY
if [ $? -eq 0 ]; then
  pass "web/static mirror matches contract public files"
else
  fail_msg "web/static mirror drift" "$(cat "$TMP_DIR/static_assets.err")" "Run: cp web/static/index.html static/index.html && cp web/static/app-state.js static/app-state.js && cp web/static/app-core.js static/app-core.js && cp web/static/app-format.js static/app-format.js && cp web/static/app.js static/app.js && cp web/static/styles.css static/styles.css && cp web/static/product-roadmap.html static/product-roadmap.html"
fi

print_header "Publish Ignore Rules"
LOCOMO_PREFLIGHT_ROOT="$ROOT" python3 - <<'PY' >"$TMP_DIR/publish_ignore.out" 2>"$TMP_DIR/publish_ignore.err"
import os
from pathlib import Path

root = Path(os.environ["LOCOMO_PREFLIGHT_ROOT"])
requirements = {
    ".gitignore": [
        ".env.local",
        "judge.conf",
        "runs/",
        "dist/",
        "outputs/",
        "external/",
        "dataset/full/",
        "web/static/*.html",
        "!web/static/index.html",
        "!web/static/product-roadmap.html",
        "static/*.html",
        "!static/index.html",
        "!static/product-roadmap.html",
    ],
    ".gitattributes": [
        "runs/ export-ignore",
        "dist/ export-ignore",
        "outputs/ export-ignore",
        "external/ export-ignore",
        "dataset/full/ export-ignore",
    ],
}
missing = {}
for rel, required in requirements.items():
    path = root / rel
    if not path.exists():
        missing[rel] = required
        continue
    lines = {
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing[rel] = [item for item in required if item not in lines]
missing = {key: value for key, value in missing.items() if value}
if missing:
    raise SystemExit(f"publish ignore rules incomplete: {missing}")
print("local secrets, run history, workspaces, external checkouts, large data, and historical static reports are ignored")
PY
if [ $? -eq 0 ]; then
  pass "publish ignore rules"
else
  fail_msg "publish ignore rules" "$(cat "$TMP_DIR/publish_ignore.err")"
fi

print_header "Open Source Docs"
LOCOMO_PREFLIGHT_ROOT="$ROOT" python3 - <<'PY' >"$TMP_DIR/open_source_docs.out" 2>"$TMP_DIR/open_source_docs.err"
import os
from pathlib import Path

root = Path(os.environ["LOCOMO_PREFLIGHT_ROOT"])
requirements = {
    "LICENSE": ["MIT License"],
    "CONTRIBUTING.md": ["OpenViking", "EchoMem", "./preflight.sh", "Do not commit"],
    "SECURITY.md": ["API keys", "private vulnerability", "./preflight.sh"],
    "CODE_OF_CONDUCT.md": ["Expected Behavior", "Unacceptable Behavior"],
    "PUBLICATION_CHECKLIST.md": ["OpenViking + EchoMem/EchoMemory", "Do Not Include", "./preflight.sh", "web/ui_contract.json"],
    ".github/pull_request_template.md": ["OpenViking", "EchoMem", "./preflight.sh", ".env.local"],
    ".github/workflows/preflight.yml": ["actions/checkout", "python3 -m py_compile", "node --check", "./preflight.sh"],
}
missing = {}
for rel, terms in requirements.items():
    path = root / rel
    if not path.exists():
        missing[rel] = terms
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    gaps = [term for term in terms if term not in text]
    if gaps:
        missing[rel] = gaps
if missing:
    raise SystemExit(f"open source docs incomplete: {missing}")
print("license, contribution, security, code-of-conduct, publication checklist, PR template, and CI preflight are present")
PY
if [ $? -eq 0 ]; then
  pass "open source docs"
else
  fail_msg "open source docs" "$(cat "$TMP_DIR/open_source_docs.err")"
fi

LOCOMO_PREFLIGHT_ROOT="$ROOT" python3 - <<'PY' >"$TMP_DIR/cache_versions.out" 2>"$TMP_DIR/cache_versions.err"
import os
import json
import re
from pathlib import Path

root = Path(os.environ["LOCOMO_PREFLIGHT_ROOT"])
versions = {}
for rel in ("web/static/index.html", "static/index.html", "web/static/product-roadmap.html", "static/product-roadmap.html"):
    text = (root / rel).read_text(encoding="utf-8", errors="ignore")
    versions[rel] = sorted(set(re.findall(r"[?&]v=([A-Za-z0-9._-]+)", text)))
all_versions = sorted({item for vals in versions.values() for item in vals})
if len(all_versions) != 1:
    raise SystemExit(f"cache versions mismatch: {versions}")
print(all_versions[0])
PY
if [ $? -eq 0 ]; then
  pass "single frontend cache version $(cat "$TMP_DIR/cache_versions.out")"
else
  fail_msg "frontend cache version mismatch" "$(cat "$TMP_DIR/cache_versions.err")"
fi

print_header "Delivery Boundary"
LOCOMO_PREFLIGHT_ROOT="$ROOT" python3 - <<'PY' >"$TMP_DIR/boundary.out" 2>"$TMP_DIR/boundary.err"
import os
import json
import re
from pathlib import Path

root = Path(os.environ["LOCOMO_PREFLIGHT_ROOT"])
include = [
    ".gitignore",
    ".gitattributes",
    "LICENSE",
    "README.md",
    "README_ECHOMEM_LOCOMO_HANDOFF.md",
    "HARNESS_SPEC.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "PUBLICATION_CHECKLIST.md",
    ".github/pull_request_template.md",
    ".github/workflows/preflight.yml",
    "web/README.md",
    "env.echomem.example",
    "env.example",
    "start.sh",
    "preflight.sh",
    "server.py",
    "dataset/manifest.json",
    "web/static",
    "static",
    "memory",
    "scripts",
]
exclude_parts = {"runs", "dist", "outputs", "external", "__pycache__", ".tmp"}
text_suffixes = {".py", ".js", ".html", ".css", ".md", ".json", ".toml", ".yaml", ".yml", ".sh", ".example", ".txt"}
contract = json.loads((root / "web/ui_contract.json").read_text(encoding="utf-8"))
public_static_files = {
    str(item)
    for item in (contract.get("delivery_boundary") or {}).get("public_static_files", [])
    if str(item).strip()
}
public_static_files |= {
    "static/" + rel.removeprefix("web/static/")
    for rel in list(public_static_files)
    if rel.startswith("web/static/")
}
secret_re = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_]{16,}|api[_-]?key\s*[:=]\s*['\"](?!<|\$\{)[^'\"<]{16,}|bearer\s+[A-Za-z0-9_\-.]{24,}", re.IGNORECASE)
retired_terms = ["后端" + "插" + "件", "插" + "件" + "已注册", "插" + "件" + "未注册"]
old_wording_re = re.compile("|".join(map(re.escape, retired_terms)))
root_readme_local_path_re = re.compile(r"(/Users/[^\s`\"'<>]+|/home/[^\s`\"'<>]+|/private/tmp/[^\s`\"'<>]+|/tmp/[^\s`\"'<>]+|[A-Za-z]:\\\\[^\s`\"'<>]+)")

def iter_paths():
    seen = set()
    for rel in include:
        path = root / rel
        if not path.exists():
            continue
        paths = path.rglob("*") if path.is_dir() else [path]
        for item in paths:
            if item.is_dir():
                continue
            rel_item = item.relative_to(root)
            if set(rel_item.parts).intersection(exclude_parts):
                continue
            key = str(rel_item)
            if (key.startswith("web/static/") or key.startswith("static/")) and key not in public_static_files:
                continue
            if item.suffix not in text_suffixes and not item.name.startswith("env."):
                continue
            if key in seen:
                continue
            seen.add(key)
            yield item

retired_hits = []
secret_hits = []
wording_hits = []
root_readme_local_path_hits = []
retired_backend_re = re.compile(r"\b" + "".join(["h", "i", "g", "o"]) + r"\b", re.IGNORECASE)
for path in iter_paths():
    rel = str(path.relative_to(root))
    text = path.read_text(encoding="utf-8", errors="ignore")
    for lineno, line in enumerate(text.splitlines(), 1):
        if retired_backend_re.search(line):
            retired_hits.append(f"{rel}:{lineno}: {line.strip()[:140]}")
        if old_wording_re.search(line):
            wording_hits.append(f"{rel}:{lineno}: {line.strip()[:140]}")
        if secret_re.search(line) and "<" not in line and "${" not in line:
            secret_hits.append(f"{rel}:{lineno}: {line.strip()[:140]}")
        if rel == "README.md" and root_readme_local_path_re.search(line):
            root_readme_local_path_hits.append(f"{rel}:{lineno}: {line.strip()[:140]}")

if retired_hits or secret_hits or wording_hits or root_readme_local_path_hits:
    if retired_hits:
        print("out-of-scope backend wording:")
        print("\n".join(retired_hits[:20]))
    if secret_hits:
        print("possible real secrets:")
        print("\n".join(secret_hits[:20]))
    if wording_hits:
        print("misleading legacy backend wording:")
        print("\n".join(wording_hits[:20]))
    if root_readme_local_path_hits:
        print("root README contains local absolute paths:")
        print("\n".join(root_readme_local_path_hits[:20]))
    raise SystemExit(1)
print("current delivery files are OpenViking + EchoMem only; no real key patterns or root README local paths found")
PY
if [ $? -eq 0 ]; then
  pass "OpenViking + EchoMem delivery boundary"
else
  fail_msg "delivery boundary scan" "$(cat "$TMP_DIR/boundary.out" "$TMP_DIR/boundary.err")"
fi

print_header "Datasets"
LOCOMO_PREFLIGHT_ROOT="$ROOT" python3 - <<'PY' >"$TMP_DIR/datasets.out" 2>"$TMP_DIR/datasets.err"
import importlib.util
import os
import sys
from pathlib import Path

root = Path(os.environ["LOCOMO_PREFLIGHT_ROOT"]).resolve()
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("locomo_server", root / "server.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["locomo_server"] = mod
spec.loader.exec_module(mod)
items = mod.dataset_registry()
locomo = next((item for item in items if item.get("id") == "locomo10"), None)
if not locomo or not locomo.get("exists"):
    raise SystemExit("dataset/locomo10.json missing")
if int(locomo.get("samples") or 0) <= 0 or int(locomo.get("questions") or 0) <= 0:
    raise SystemExit(f"LoCoMo dataset has invalid counts: {locomo}")
for item in items:
    status = "OK" if item.get("exists") else "MISSING"
    print(f"{status} {item.get('id')}: {item.get('samples')} samples / {item.get('questions')} questions")
PY
if [ $? -eq 0 ]; then
  pass "dataset registry"
  sed 's/^/     /' "$TMP_DIR/datasets.out" | head -12
else
  fail_msg "dataset registry" "$(cat "$TMP_DIR/datasets.err")"
fi

print_header "Local Port"
if command -v lsof >/dev/null 2>&1; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    pass "web port $PORT has a listener"
  else
    warn_msg "web service is not running" "Start it with: LOCOMO_EVAL_PORT=$PORT ./start.sh"
  fi
else
  warn_msg "lsof not found" "Skipping local port check."
fi

print_header "Web API Gates"
if command -v curl >/dev/null 2>&1; then
  if curl -fsS "$BASE_URL/health" >"$TMP_DIR/health.json" 2>"$TMP_DIR/curl.err"; then
    pass "health endpoint"
    for endpoint in "/api/backends" "/api/adapter-doctor" "/api/handoff-audit" "/api/readiness" "/api/handoff-dashboard" "/api/github-launch-kit" "/api/locomo-flow-status" "/api/acceptance-matrix" "/api/smoke-plan" "/api/handoff-package" "/api/echomem-contract" "/api/agent-alignment" "/api/account-isolation"; do
      name="${endpoint#/api/}"
      if curl -fsS "$BASE_URL$endpoint" >"$TMP_DIR/${name}.json" 2>"$TMP_DIR/${name}.err"; then
        python3 - "$TMP_DIR/${name}.json" "$endpoint" <<'PY' >"$TMP_DIR/${name}.status" 2>"$TMP_DIR/${name}.status.err"
import json
import sys
path, endpoint = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
status = str(data.get("status") or "")
score = data.get("score")
if endpoint == "/api/backends":
    backends = data.get("backends") or []
    ids = {item.get("id") for item in backends}
    failures = [
        f"{item.get('id')}:{(item.get('contract') or {}).get('status')}"
        for item in backends
        if (item.get("contract") or {}).get("status") == "fail"
    ]
    print(f"{','.join(sorted(ids))} contracts={len(backends) - len(failures)}/{len(backends)}")
    if ids != {"openviking", "echomemory"} or failures:
        raise SystemExit(1)
elif endpoint == "/api/adapter-doctor":
    ids = set(data.get("registered_backends") or [])
    print(f"{status} registered={','.join(sorted(ids))}")
    if status != "ok" or ids != {"openviking", "echomemory"}:
        raise SystemExit(1)
elif endpoint in {"/api/readiness", "/api/handoff-dashboard", "/api/github-launch-kit", "/api/locomo-flow-status", "/api/acceptance-matrix", "/api/smoke-plan", "/api/handoff-package", "/api/agent-alignment", "/api/account-isolation"}:
    print(f"{status} score={score}")
    if status == "fail":
        raise SystemExit(1)
elif status != "ok":
    print(status)
    raise SystemExit(1)
else:
    print(status)
PY
        if [ $? -eq 0 ]; then
          pass "$endpoint $(cat "$TMP_DIR/${name}.status")"
        else
          fail_msg "$endpoint status" "$(cat "$TMP_DIR/${name}.status" "$TMP_DIR/${name}.status.err")"
        fi
      else
        fail_msg "$endpoint" "$(cat "$TMP_DIR/${name}.err")"
      fi
    done
    agent_status="$(
      curl -sS -o "$TMP_DIR/agent_echomemory_context.json" -w "%{http_code}" \
        -X POST "$BASE_URL/api/agent/context" \
        -H "Content-Type: application/json" \
        --data '{"backend":"echomemory","memoryBackend":"echomemory","messages":[{"role":"user","content":"preflight route check"}]}' \
        2>"$TMP_DIR/agent_echomemory_context.err"
    )"
    if [ "$agent_status" = "200" ]; then
      python3 - "$TMP_DIR/agent_echomemory_context.json" <<'PY' >"$TMP_DIR/agent_echomemory_context.status" 2>"$TMP_DIR/agent_echomemory_context.status.err"
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
if data.get("backend") != "echomemory":
    raise SystemExit(f"unexpected backend route: {data.get('backend')!r}")
trace = data.get("context_trace") or {}
if not str(trace.get("phase") or "").startswith("echomemory-"):
    raise SystemExit(f"missing EchoMemory context_trace phase: {trace}")
if not isinstance(data.get("retrieval"), dict):
    raise SystemExit("EchoMemory agent context must include retrieval payload")
text = json.dumps(data, ensure_ascii=False)
if "尚未实现对话 Agent 工作台" in text:
    raise SystemExit("EchoMemory agent route still returns unsupported-workbench payload")
retired_backend = "".join(["h", "i", "g", "o"])
if retired_backend in text.lower():
    raise SystemExit("EchoMemory agent route includes retired backend wording")
print("echomemory agent workbench returns context trace")
PY
      if [ $? -eq 0 ]; then
        pass "/api/agent/context EchoMem route gate $(cat "$TMP_DIR/agent_echomemory_context.status")"
      else
        fail_msg "/api/agent/context EchoMem route gate" "$(cat "$TMP_DIR/agent_echomemory_context.status" "$TMP_DIR/agent_echomemory_context.status.err")"
      fi
    else
      fail_msg "/api/agent/context EchoMem route gate" "expected HTTP 200 for EchoMem agent workbench, got ${agent_status:-empty}" "$(cat "$TMP_DIR/agent_echomemory_context.json" "$TMP_DIR/agent_echomemory_context.err" 2>/dev/null | head -10)"
    fi
  else
    warn_msg "web API gates skipped" "Service not reachable at $BASE_URL. Start it with: LOCOMO_EVAL_PORT=$PORT ./start.sh"
  fi
else
  warn_msg "curl not found" "Skipping Web API checks."
fi

print_header "Summary"
printf "Passed: %s  Warnings: %s  Failed: %s\n" "$ok" "$warn" "$fail"
if [ "$fail" -gt 0 ]; then
  echo "Preflight failed. Fix required failures before sharing or running external tests."
  exit 1
fi
if [ "$warn" -gt 0 ]; then
  echo "Preflight passed with warnings."
else
  echo "Preflight passed."
fi
