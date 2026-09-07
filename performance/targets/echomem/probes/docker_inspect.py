"""Read container limits through the Docker CLI or local Unix socket."""

import http.client
import json
import shutil
import socket
import subprocess
import time
import urllib.request
from urllib.parse import quote


def inspect_container(name: str) -> dict:
    if not name:
        raise ValueError("A test container name is required")
    if shutil.which("docker"):
        return json.loads(subprocess.check_output(
            ["docker", "inspect", name], timeout=15, text=True
        ))[0]
    return _socket_json("/containers/" + quote(name, safe="") + "/json")


def _socket_json(path: str) -> dict:
    connection = http.client.HTTPConnection("localhost", timeout=15)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(15)
    try:
        sock.connect("/var/run/docker.sock")
        connection.sock = sock
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"Docker API returned HTTP {response.status}")
        return json.loads(response.read())
    finally:
        connection.close()
        sock.close()


def resource_sample(name: str) -> dict:
    """Use the same local Docker socket; export no container configuration."""
    return resource_values(_socket_json("/containers/" + quote(name, safe="") + "/stats?stream=false"))


def resource_values(stats: dict) -> dict:
    current, previous = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
    used = current.get("cpu_usage", {}).get("total_usage")
    prior = previous.get("cpu_usage", {}).get("total_usage")
    system, prior_system = current.get("system_cpu_usage"), previous.get("system_cpu_usage")
    cpus = current.get("online_cpus")
    cpu_percent = None
    if all(isinstance(v, (int, float)) for v in (used, prior, system, prior_system, cpus)):
        if system > prior_system and used >= prior:
            cpu_percent = (used - prior) / (system - prior_system) * cpus * 100
    memory = stats.get("memory_stats", {})
    detail = memory.get("stats", {})
    usage = memory.get("usage")
    cache = detail.get("inactive_file", detail.get("total_inactive_file"))
    throttling = current.get("throttling_data", {})
    return {"cpu_percent_one_core_100": cpu_percent, "memory_usage_bytes": usage,
            "memory_limit_bytes": memory.get("limit"), "rss_bytes": detail.get("rss", detail.get("anon")),
            "working_set_bytes": max(0, usage-cache) if usage is not None and cache is not None else None,
            "pids": stats.get("pids_stats", {}).get("current"),
            "cpu_periods_total": throttling.get("periods"),
            "cpu_throttled_periods_total": throttling.get("throttled_periods"),
            "cpu_throttled_time_ns_total": throttling.get("throttled_time"),
            "swap_bytes": detail.get("swap", detail.get("total_swap"))}


def restart_container(name: str, ready_url: str, *, timeout_s: float = 180) -> dict:
    """Restart a dedicated target via Docker socket and wait for readiness."""
    if shutil.which("docker"):
        subprocess.check_call(["docker", "restart", "--time", "30", name],
                              stdout=subprocess.DEVNULL)
    else:
        connection = http.client.HTTPConnection("localhost", timeout=40)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(40)
        try:
            sock.connect("/var/run/docker.sock")
            connection.sock = sock
            connection.request("POST", "/containers/" + quote(name, safe="") + "/restart?t=30")
            response = connection.getresponse()
            response.read()
            if response.status != 204:
                raise RuntimeError(f"Docker restart returned HTTP {response.status}")
        finally:
            connection.close()
            sock.close()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(ready_url, timeout=3) as response:
                if response.status == 200:
                    return {"status": "ready", "elapsed_s": timeout_s - max(0, deadline-time.monotonic())}
        except Exception:
            pass
        time.sleep(2)
    return {"status": "timeout", "elapsed_s": timeout_s}
