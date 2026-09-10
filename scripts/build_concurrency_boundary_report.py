"""Render persisted topology/boundary probe evidence without rerunning requests."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from performance.targets.echomem.orchestrator.report import write_objective_suite_html


def build(root: Path) -> dict:
    profiles = []
    for kind, level in [("topology", n) for n in (16, 32, 64, 128)] + [("boundary", 32)]:
        folder = root / f"{kind}-{level}"
        def load(name):
            path = folder / name
            return json.loads(path.read_text()) if path.exists() else {}
        manifest = load("manifest.json")
        filename = "concurrency-topology.json" if kind == "topology" else "payload-boundary.json"
        payload = load(filename)
        seeds = load("seed.json") or []
        resources = load("resources.json") or []
        profile = {"name": f"{kind}-{level}", "objectives": [{
            "id": kind, "name": f"{level} 档", "status": payload.get("status", "NOT_RUN"),
            "reason": "缺少结果，未计为完成" if not payload else "已保留完整请求分母和最终状态；不以 HTTP 2xx 代表语义成功",
            "observed": {"deployment": manifest,
                "seed_completed": sum(s.get("terminal") == "completed" for s in seeds),
                "seed_strict_quality_ok": sum(bool(s.get("quality_ok")) for s in seeds),
                "seed_total": len(seeds), "resource_samples": len(resources),
                "cpu_peak_percent": max((float(s.get("CPUPerc", "0%").rstrip("%")) for s in resources), default=None),
                "memory_peak_percent": max((float(s.get("MemPerc", "0%").rstrip("%")) for s in resources), default=None)},
            "owner": "实测服务与测试平台证据", "evidence": str(folder / filename),
        }]}
        if payload:
            profile["concurrency_topology" if kind == "topology" else "payload_boundary"] = payload
        profiles.append(profile)
    return {"title": "EchoMem 并发拓扑与超长请求实测",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "16/32/64/128 参数档；四种用户/Session拓扑；1 MiB Commit/add_memory；0–1 MiB 文本/二进制边界。不包含 M1–M6 全套。",
        "summary": "每档独立实例与租户。先写入会议室 Cedar 的自然语言事实并 Commit，再问会议室名称；128 次操作/拓扑。异构场景混合 Search 和 64 KiB Commit。Commit 轮询预算45秒，未完成保留为待排空，绝不当作成功。单独边界实例的1 MiB Commit轮询预算120秒。各档仅为短窗口观测，不代表稳定容量上限。",
        "profiles": profiles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    write_objective_suite_html(result, args.out)


if __name__ == "__main__":
    main()
