"""Shared identity reuse for benchmark resume.

When a benchmark run resumes from a prior run, the harness does not provision
a fresh tenant identity.  The prior run's credentials are needed to access
its memory sessions, so they are loaded from the prior run's resume manifest
and applied to the memory client.
"""

from __future__ import annotations

import json
from pathlib import Path


def apply_resume_memory_identity(
    client,
    resume_source: str,
    log,
) -> None:
    """Load account/user_id/auth_key from a prior run's resume manifest.

    The source may be a run directory (holding ``qa_resume_manifest.json``)
    or a file inside it.  A missing manifest leaves the client untouched.
    """
    source = Path(resume_source)
    manifest_path = (
        source / "qa_resume_manifest.json"
        if source.is_dir()
        else source.parent / "qa_resume_manifest.json"
    )
    if not manifest_path.is_file():
        log.warning(
            "Resume source has no qa_resume_manifest.json: %s — identity not "
            "reused, memory may live under a different tenant/user (runs "
            "created before the manifest-write fix, or interrupted before the "
            "manifest was written)",
            manifest_path,
        )
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    identity = manifest.get("memory_identity") or {}
    account = str(identity.get("account") or "").strip()
    user_id = str(identity.get("user_id") or "").strip()
    auth_key = str(identity.get("auth_key") or "").strip()
    if account:
        client.account = account
    if user_id:
        client.user_id = user_id
    if auth_key:
        client.auth_key = auth_key
    if account or auth_key:
        log.info(
            "Resumed memory identity from prior run: account=%s user=%s",
            client.account,
            client.user_id,
        )
