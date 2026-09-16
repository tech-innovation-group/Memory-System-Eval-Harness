#!/usr/bin/env python3
"""Serve only the current EchoMem robot report from a runs directory.

The runner keeps private tenant identities beside each report.  A plain
``http.server`` rooted at a run directory can expose those credentials and
also remains pinned to an old run after the robot starts a new one.  This
server resolves the newest run per request and exposes a small allowlist of
public evidence files.
"""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import re
from socketserver import ThreadingMixIn
from typing import Optional
from urllib.parse import unquote, urlsplit


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


RUN_NAME = re.compile(r"pr(?P<pr>\d+)-(?P<stamp>\d{8}T\d{6}Z)$")
PUBLIC_FILES = {
    "report.html",
    "report.json",
    "robot-status.json",
    "summary.json",
    "suite.json",
    "execution-manifest.json",
    "git-revisions.txt",
    "metrics_samples.csv",
    "structured-stage-events.jsonl",
}
PUBLIC_ALIASES = {"report.json": "summary.json"}


def latest_run(runs_dir: Path, pr_number: Optional[int] = None) -> Optional[Path]:
    candidates = []
    for path in runs_dir.iterdir() if runs_dir.is_dir() else ():
        if not path.is_dir():
            continue
        match = RUN_NAME.fullmatch(path.name)
        if not match or (pr_number is not None and int(match["pr"]) != pr_number):
            continue
        candidates.append((match.group("stamp"), path.name, path))
    return max(candidates, default=None)[2] if candidates else None


def public_file(path: str) -> Optional[str]:
    requested = unquote(urlsplit(path).path).lstrip("/")
    if requested == "":
        requested = "report.html"
    return requested if requested in PUBLIC_FILES else None


class ReportHandler(BaseHTTPRequestHandler):
    server_version = "EchoMemReport/1"

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404, "Report file is not ready")
            return
        body = path.read_bytes()
        content_type = (
            "text/html; charset=utf-8" if path.suffix == ".html" else
            "text/csv; charset=utf-8" if path.suffix == ".csv" else
            "application/x-ndjson" if path.suffix == ".jsonl" else
            "application/json"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        self._handle()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        self._handle()

    def _handle(self) -> None:
        requested = public_file(self.path)
        if requested is None:
            self.send_error(404, "Only the public EchoMem report is available")
            return
        config = self.server.report_config  # type: ignore[attr-defined]
        run = latest_run(config["runs_dir"], config["pr_number"])
        if run is None:
            self.send_error(404, "No EchoMem run is available")
            return
        self._send_file(run / PUBLIC_ALIASES.get(requested, requested))

    def log_message(self, format: str, *args) -> None:
        return


def build_server(host: str, port: int, runs_dir: Path, pr_number: Optional[int]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), ReportHandler)
    server.report_config = {"runs_dir": runs_dir, "pr_number": pr_number}  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18181)
    args = parser.parse_args()
    server = build_server(args.host, args.port, args.runs_dir, args.pr)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
