"""Expand the explicitly requested M1-M3 topology and payload test contract."""
from copy import deepcopy


def expand_extended_profile(profile, selected):
    result = deepcopy(profile)
    if not result.get("extended_load_tests"):
        return result
    if not {"M1", "M2", "M3"}.issubset(selected):
        raise ValueError("extended_load_tests requires M1,M2,M3")
    topology = {"enabled": True, "levels": [16, 64], "max_concurrency": 64,
                "requests_per_level": 128, "sessions_per_user": 2,
                "within_session_concurrency": 4, "stop_after_boundary": False,
                "require_recall_quality": True, "large_commit_chars": 65536,
                "commit_poll_timeout_s": 120,
                **result.get("concurrency_topology", {})}
    if not topology.get("enabled") or not {16, 64}.issubset(topology.get("levels", [])):
        raise ValueError("extended_load_tests must include enabled 16/64 topology tests")
    if topology.get("stop_after_boundary") or topology.get("topologies"):
        raise ValueError("extended_load_tests must retain all four topologies and both levels")
    payload = {"enabled": True, "sizes_bytes": [0, 1, 1024, 65536, 262144, 524288, 1048576],
               "commit_content_chars": 1048576, "commit_chunk_chars": 262144,
               "commit_timeout_s": 600, "mcp_add_memory_chars": 1048576,
               **result.get("payload_boundary", {})}
    if not payload.get("enabled") or not payload.get("mcp_base_url"):
        raise ValueError("extended_load_tests requires enabled payload_boundary with mcp_base_url")
    if payload.get("skip_long_commit") or payload.get("skip_mcp"):
        raise ValueError("extended_load_tests cannot skip long Commit or MCP add_memory")
    if not {0, 1048576}.issubset(payload.get("sizes_bytes", [])):
        raise ValueError("extended_load_tests must include 0 and 1MiB payload boundaries")
    if min(payload["commit_content_chars"], payload["mcp_add_memory_chars"]) < 1048576:
        raise ValueError("extended_load_tests requires at least 1MiB-character long writes")
    result.update(concurrency_topology=topology, payload_boundary=payload)
    return result
