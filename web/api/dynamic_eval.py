"""HTTP API handlers for dynamic evaluation endpoints.

This module provides API routes for:
- POST /api/dynamic/generate_background_memories
- POST /api/dynamic/generate_user_query
- POST /api/dynamic/evaluate_response
- POST /api/dynamic/inject_memories
- POST /api/dynamic/echo_agent (proxy to EchoAgent backend)
- GET /api/dynamic/evaluators
- GET /api/dynamic/evaluators/:id
- DELETE /api/dynamic/evaluators/:id
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_EVALUATOR_ID_RE = re.compile(r"^/api/dynamic/evaluators/([^/]+)$")


def _inject_memories_to_echomem(
    echomem_url: str,
    agent_id: str,
    session_id: str,
    memories: list[dict[str, Any]],
    user_id: str,
    evaluator_id: str | None = None,
) -> dict[str, Any]:
    """Inject background memories into EchoMem for retrieval.
    
    This opens a session, adds messages containing the memories, and commits them.
    Uses the EchoMem direct API with auth key lookup from echoagent_registry.json.
    
    If the session already has committed archives, skip injection.
    
    Args:
        evaluator_id: If provided, check stop flag for this evaluator during polling.
    """
    from pathlib import Path
    from memory.dynamic_evaluator import is_evaluator_stopped
    
    print(f"[inject_memories] Function called with base_url={echomem_url}, session_id={session_id}, memories={len(memories)}, user_id={user_id}")
    base_url = echomem_url.rstrip("/")
    
    # Try to find auth_key from echoagent_registry.json
    auth_key = None
    registry_paths = [
        Path(__file__).resolve().parent.parent.parent.parent / "EchoMem" / "echoagent_registry.json",
        Path(__file__).resolve().parent.parent.parent / "EchoMem" / "echoagent_registry.json",
        Path("EchoMem/echoagent_registry.json"),
        Path("../EchoMem/echoagent_registry.json"),
    ]
    
    for registry_path in registry_paths:
        print(f"[inject_memories] Checking registry path: {registry_path}, exists: {registry_path.exists()}")
        if registry_path.exists():
            try:
                with registry_path.open("r", encoding="utf-8") as f:
                    registry = json.load(f)
                print(f"[inject_memories] Registry keys: {list(registry.keys())}")
                print(f"[inject_memories] Looking for user_id: {user_id}")
                # The registry key is EchoAgent's userId (UUID), not EchoMem's internal user_id
                # Try exact match first
                if user_id in registry:
                    auth_key = registry[user_id].get("auth_key")
                    print(f"[inject_memories] Found auth_key for user_id={user_id}")
                else:
                    # Try to find by partial match (in case user_id format differs)
                    for key in registry:
                        if user_id in key or key in user_id:
                            auth_key = registry[key].get("auth_key")
                            print(f"[inject_memories] Found auth_key via partial match: {key}")
                            break
                    if not auth_key and "anonymous" in registry:
                        auth_key = registry["anonymous"].get("auth_key")
                        print(f"[inject_memories] Using anonymous auth_key")
                break
            except Exception as e:
                print(f"[inject_memories] Failed to read registry: {e}")
    
    if not auth_key:
        print(f"[inject_memories] No auth_key found, trying to create tenant/user")
        # Create tenant and user via EchoMem auth API
        try:
            # Create tenant
            tenant_url = f"{base_url}/api/auth/tenants"
            req = Request(tenant_url, data=json.dumps({}).encode("utf-8"), 
                         headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=30) as resp:
                tenant_result = json.loads(resp.read().decode("utf-8"))
            tenant_id = tenant_result.get("tenant_id")
            print(f"[inject_memories] Created tenant: {tenant_id}")
            
            # Create user
            user_url = f"{base_url}/api/auth/tenants/{tenant_id}/users"
            req = Request(user_url, data=json.dumps({"user_id": user_id}).encode("utf-8"),
                         headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=30) as resp:
                user_result = json.loads(resp.read().decode("utf-8"))
            
            # Get auth key
            key_url = f"{base_url}/api/auth/tenants/{tenant_id}/users/{user_id}/key"
            req = Request(key_url, data=json.dumps({}).encode("utf-8"),
                         headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=30) as resp:
                key_result = json.loads(resp.read().decode("utf-8"))
            auth_key = key_result.get("auth_key")
            print(f"[inject_memories] Created user and got auth_key")
        except Exception as e:
            return {"success": False, "error": f"Failed to create auth: {e}"}
    
    if not auth_key:
        return {"success": False, "error": "No auth_key available"}
    
    # Check if session already has committed archives (skip injection if exists)
    archives_url = f"{base_url}/api/sessions/{session_id}/archives"
    try:
        req = Request(archives_url, headers={"X-Auth-Key": auth_key}, method="GET")
        with urlopen(req, timeout=30) as response:
            archives_result = json.loads(response.read().decode("utf-8"))
            archives = archives_result.get("archives", [])
            if archives:
                # Found existing archives, skip injection
                print(f"[inject_memories] Session {session_id} already has {len(archives)} archives, skipping injection")
                return {
                    "success": True,
                    "session_id": session_id,
                    "messages_added": 0,
                    "memories_count": 0,
                    "auth_key": auth_key,
                    "skipped": True,
                    "reason": f"Session already has {len(archives)} committed archives",
                }
    except Exception as e:
        # Session doesn't exist or other error, continue with injection
        print(f"[inject_memories] No existing archives found, proceeding with injection: {e}")
    
    # Step 1: Open session
    open_url = f"{base_url}/api/sessions/open"
    open_data = {
        "agent_id": agent_id,
        "session_id": session_id,
    }
    
    try:
        req = Request(
            open_url,
            data=json.dumps(open_data).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Auth-Key": auth_key},
            method="POST",
        )
        with urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            print(f"[inject_memories] Opened session: {result}")
    except Exception as e:
        return {"success": False, "error": f"Failed to open session: {e}"}
    
    # Step 2: Add messages (each memory as a user message)
    messages_added = 0
    for i, memory in enumerate(memories):
        memory_text = memory.get("text", "")
        if not memory_text:
            continue
        
        msg_url = f"{base_url}/api/sessions/{session_id}/messages"
        msg_data = {
            "role": "user",
            "content": memory_text,
        }
        
        try:
            req = Request(
                msg_url,
                data=json.dumps(msg_data).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Auth-Key": auth_key},
                method="POST",
            )
            with urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                messages_added += 1
        except Exception as e:
            print(f"[inject_memories] Failed to add message {i}: {e}")
    
    print(f"[inject_memories] Added {messages_added} messages")
    
    # Step 3: Commit to extract memories
    commit_url = f"{base_url}/api/sessions/{session_id}/commit"
    archive_id = None
    try:
        req = Request(
            commit_url,
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Auth-Key": auth_key},
            method="POST",
        )
        with urlopen(req, timeout=120) as response:  # Longer timeout for commit
            result = json.loads(response.read().decode("utf-8"))
            archive_id = result.get("result", {}).get("archive_id")
            print(f"[inject_memories] Commit result: {result}, archive_id: {archive_id}")
    except Exception as e:
        return {"success": False, "error": f"Failed to commit: {e}", "messages_added": messages_added}
    
    # Step 4: Poll for commit completion (no timeout - injection can take a long time)
    if archive_id:
        import time
        status_url = f"{base_url}/api/sessions/{session_id}/commits/{archive_id}"
        poll_interval = 3  # Poll every 3 seconds
        
        print(f"[inject_memories] Waiting for commit to complete, archive_id={archive_id}")
        while True:
            # Check if evaluator has been stopped
            if evaluator_id and is_evaluator_stopped(evaluator_id):
                print(f"[inject_memories] Evaluator {evaluator_id} stopped, aborting poll")
                return {"success": False, "error": "Evaluation stopped by user", "messages_added": messages_added, "stopped": True}
            
            try:
                req = Request(
                    status_url,
                    headers={"X-Auth-Key": auth_key},
                    method="GET",
                )
                with urlopen(req, timeout=30) as response:
                    status_result = json.loads(response.read().decode("utf-8"))
                    # EchoMem returns {"status": {"status": "completed", ...}}
                    # Handle both nested and flat structures
                    if isinstance(status_result.get("status"), dict):
                        status_obj = status_result.get("status", {})
                        status = status_obj.get("status", "unknown")
                        stage = status_obj.get("stage", "")
                        error = status_obj.get("error")
                    else:
                        status = status_result.get("status", "unknown")
                        stage = status_result.get("stage", "")
                        error = status_result.get("error")
                    
                    print(f"[inject_memories] Commit status: {status}, stage: {stage}")
                    
                    if status == "completed" or status == "success":
                        print(f"[inject_memories] Commit completed successfully")
                        break
                    elif status == "failed" or status == "error":
                        error_msg = error or "Unknown error"
                        return {"success": False, "error": f"Commit failed: {error_msg}", "messages_added": messages_added}
                    
                    time.sleep(poll_interval)
            except Exception as e:
                print(f"[inject_memories] Error checking commit status: {e}")
                time.sleep(poll_interval)
    
    return {
        "success": True,
        "session_id": session_id,
        "messages_added": messages_added,
        "memories_count": len(memories),
        "auth_key": auth_key,
        "archive_id": archive_id,
    }


def _proxy_to_echo_agent(base_url: str, method: str, path: str, headers: dict[str, str], body: str | None = None, cookies: list[str] | None = None) -> dict[str, Any]:
    """Proxy a request to EchoAgent backend.

    Args:
        base_url: EchoAgent base URL (e.g., http://127.0.0.1:31020)
        method: HTTP method (GET, POST, PUT, DELETE)
        path: Request path (e.g., /v1/auth/login)
        headers: Request headers
        body: Request body (for POST/PUT)
        cookies: Cookies to send with the request

    Returns:
        Response dict with status, headers, and body
    """
    import http.cookiejar
    import urllib.request
    
    url = f"{base_url.rstrip('/')}{path}"
    req_headers = {"Content-Type": "application/json"}
    req_headers.update({k: v for k, v in headers.items() if k.lower() != "content-type"})
    
    # Add cookies if provided
    if cookies:
        req_headers["Cookie"] = "; ".join(cookies)

    try:
        # Create a cookie jar to handle session cookies
        cookie_jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        
        req = Request(
            url,
            data=body.encode("utf-8") if body else None,
            headers=req_headers,
            method=method,
        )
        
        with opener.open(req, timeout=60) as response:
            response_body = response.read().decode("utf-8")
            response_headers = dict(response.headers)
            
            # Extract cookies from cookie jar
            resp_cookies = [f"{c.name}={c.value}" for c in cookie_jar]
            
            try:
                response_json = json.loads(response_body)
            except json.JSONDecodeError:
                response_json = {"raw": response_body}
            
            return {
                "status": response.status,
                "headers": response_headers,
                "body": response_json,
                "cookies": resp_cookies,
            }
    except HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        try:
            error_json = json.loads(error_body)
        except json.JSONDecodeError:
            error_json = {"error": error_body}
        return {
            "status": e.code,
            "headers": dict(e.headers) if e.headers else {},
            "body": error_json,
            "cookies": [],
        }
    except URLError as e:
        return {
            "status": 503,
            "headers": {},
            "body": {"error": f"Connection failed: {e.reason}"},
            "cookies": [],
        }
    except Exception as e:
        return {
            "status": 500,
            "headers": {},
            "body": {"error": str(e)},
            "cookies": [],
        }


def handle_dynamic_eval_post(
    path: str,
    payload: dict[str, Any],
    *,
    send_json,
    create_evaluator,
    get_evaluator,
    generate_background_memories,
    generate_next_query,
    evaluate_response=None,
) -> bool:
    """Handle POST requests for dynamic evaluation endpoints.

    Args:
        path: Request path
        payload: Request body
        send_json: Function to send JSON response
        create_evaluator: Function to create a new evaluator
        get_evaluator: Function to get an evaluator by ID
        generate_background_memories: Function to generate background memories
        generate_next_query: Function to generate next query
        evaluate_response: Function to evaluate response quality (optional)

    Returns:
        True if the path was handled, False otherwise
    """
    if path == "/api/dynamic/generate_background_memories":
        try:
            config = payload.get("config", {})
            evaluator_id = create_evaluator(config)
            evaluator = get_evaluator(evaluator_id)
            if not evaluator:
                send_json({"error": "Failed to create evaluator"}, 500)
                return True

            result = generate_background_memories(evaluator)
            result["evaluator_id"] = evaluator_id
            send_json(result)
        except Exception as exc:
            send_json({"error": str(exc)}, 500)
        return True

    if path == "/api/dynamic/generate_user_query":
        try:
            evaluator_id = payload.get("evaluator_id", "")
            if not evaluator_id:
                send_json({"error": "evaluator_id is required"}, 400)
                return True

            evaluator = get_evaluator(evaluator_id)
            if not evaluator:
                send_json({"error": f"Evaluator not found: {evaluator_id}"}, 404)
                return True

            context = payload.get("context", {})
            result = generate_next_query(evaluator, context)
            send_json(result)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[generate_user_query] Error: {exc}")
            send_json({"error": str(exc)}, 500)
        return True

    if path == "/api/dynamic/evaluate_response":
        try:
            evaluator_id = payload.get("evaluator_id", "")
            query = payload.get("query", "")
            reply = payload.get("reply", "")
            ground_facts = payload.get("ground_facts", [])
            recalled_memories = payload.get("recalled_memories", [])

            if not evaluator_id:
                send_json({"error": "evaluator_id is required"}, 400)
                return True

            evaluator = get_evaluator(evaluator_id)
            if not evaluator:
                send_json({"error": f"Evaluator not found: {evaluator_id}"}, 404)
                return True

            # Use evaluator's LLM-based evaluation method
            result = evaluator.evaluate_response(query, reply, ground_facts, recalled_memories)
            send_json(result)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[evaluate_response] Error: {exc}")
            send_json({"error": str(exc)}, 500)
        return True

    if path == "/api/dynamic/inject_memories":
        try:
            echomem_url = payload.get("echomem_url", "http://127.0.0.1:8010")
            agent_id = payload.get("agent_id", "dynamic_eval")
            session_id = payload.get("session_id")
            memories = payload.get("memories", [])
            user_id = payload.get("user_id", "eval_user")
            evaluator_id = payload.get("evaluator_id")  # Optional: for stop flag checking

            print(f"[inject_memories] API called: echomem_url={echomem_url}, agent_id={agent_id}, session_id={session_id}, memories_count={len(memories)}, user_id={user_id}")

            if not session_id:
                send_json({"error": "session_id is required"}, 400)
                return True

            if not memories:
                send_json({"error": "memories are required"}, 400)
                return True

            # Inject memories into EchoMem via HTTP API
            result = _inject_memories_to_echomem(
                echomem_url=echomem_url,
                agent_id=agent_id,
                session_id=session_id,
                memories=memories,
                user_id=user_id,
                evaluator_id=evaluator_id,
            )
            send_json(result)
        except Exception as exc:
            send_json({"error": str(exc)}, 500)
        return True

    # EchoAgent proxy endpoint
    if path == "/api/dynamic/echo_agent":
        try:
            base_url = payload.get("base_url", "http://127.0.0.1:31020")
            method = payload.get("method", "GET")
            request_path = payload.get("path", "")
            req_headers = payload.get("headers", {})
            body = payload.get("body")
            cookies = payload.get("cookies", [])

            if not request_path:
                send_json({"error": "path is required"}, 400)
                return True

            result = _proxy_to_echo_agent(base_url, method, request_path, req_headers, body, cookies)
            send_json(result, status=result.get("status", 200))
        except Exception as exc:
            send_json({"error": str(exc)}, 500)
        return True

    # Handle evaluator management via POST (for operations)
    match = _EVALUATOR_ID_RE.match(path)
    if match:
        evaluator_id = match.group(1)
        action = payload.get("action", "")

        if action == "add_history":
            evaluator = get_evaluator(evaluator_id)
            if not evaluator:
                send_json({"error": f"Evaluator not found: {evaluator_id}"}, 404)
                return True

            query = payload.get("query", "")
            reply = payload.get("reply", "")
            if query and reply:
                evaluator.add_to_history(query, reply)
            send_json({"status": "ok"})
            return True

        if action == "get_state":
            evaluator = get_evaluator(evaluator_id)
            if not evaluator:
                send_json({"error": f"Evaluator not found: {evaluator_id}"}, 404)
                return True

            send_json(evaluator.get_state())
            return True

        if action == "stop":
            # Set stop flag for this evaluator
            from memory.dynamic_evaluator import set_evaluator_stopped
            set_evaluator_stopped(evaluator_id, True)
            send_json({"status": "stopped", "evaluator_id": evaluator_id})
            return True

        if action == "clear_stop":
            # Clear stop flag for this evaluator
            from memory.dynamic_evaluator import clear_evaluator_stop_flag
            clear_evaluator_stop_flag(evaluator_id)
            send_json({"status": "cleared", "evaluator_id": evaluator_id})
            return True

    return False


def handle_dynamic_eval_get(
    path: str,
    *,
    send_json,
    get_evaluator,
    list_evaluators,
    remove_evaluator,
) -> bool:
    """Handle GET requests for dynamic evaluation endpoints.

    Args:
        path: Request path
        send_json: Function to send JSON response
        get_evaluator: Function to get an evaluator by ID
        list_evaluators: Function to list all evaluators
        remove_evaluator: Function to remove an evaluator

    Returns:
        True if the path was handled, False otherwise
    """
    # Endpoint: List available user simulator configs
    if path == "/api/dynamic/user_simulators":
        try:
            from memory.prompt_config_loader import list_available_simulators
            simulators = list_available_simulators()
            send_json({"simulators": simulators})
        except Exception as exc:
            send_json({"error": str(exc)}, 500)
        return True

    # Endpoint: List available evaluator configs
    if path == "/api/dynamic/evaluator_configs":
        try:
            from memory.prompt_config_loader import list_available_evaluators
            evaluators = list_available_evaluators()
            send_json({"evaluator_configs": evaluators})
        except Exception as exc:
            send_json({"error": str(exc)}, 500)
        return True

    if path == "/api/dynamic/evaluators":
        try:
            evaluators = list_evaluators()
            send_json({"evaluators": evaluators})
        except Exception as exc:
            send_json({"error": str(exc)}, 500)
        return True

    match = _EVALUATOR_ID_RE.match(path)
    if match:
        evaluator_id = match.group(1)
        evaluator = get_evaluator(evaluator_id)
        if not evaluator:
            send_json({"error": f"Evaluator not found: {evaluator_id}"}, 404)
            return True

        send_json(evaluator.get_state())
        return True

    return False


def _default_evaluate_response(
    query: str,
    reply: str,
    ground_facts: list[dict[str, Any]],
    recalled_memories: list[dict[str, Any]],
) -> dict[str, Any]:
    """Default evaluation: check if ground facts are mentioned in reply.
    
    Args:
        query: User query
        reply: Model reply
        ground_facts: Expected facts that should be in the answer
        recalled_memories: Memories that were recalled during inference
        
    Returns:
        Evaluation result with score and reason
    """
    reply_lower = reply.lower()
    matched_facts = 0
    total_facts = len(ground_facts)
    matched_details = []
    
    for fact in ground_facts:
        fact_text = fact.get("text", "") or fact.get("fact", "") or str(fact)
        if not fact_text:
            continue
        # Simple keyword matching
        keywords = _extract_keywords(fact_text)
        matched_keywords = [kw for kw in keywords if kw in reply_lower]
        if len(matched_keywords) >= len(keywords) * 0.5:  # At least 50% keywords matched
            matched_facts += 1
            matched_details.append({
                "fact": fact_text[:100] + "..." if len(fact_text) > 100 else fact_text,
                "matched": True,
                "keywords_matched": matched_keywords,
            })
        else:
            matched_details.append({
                "fact": fact_text[:100] + "..." if len(fact_text) > 100 else fact_text,
                "matched": False,
                "keywords_matched": matched_keywords,
            })
    
    # Calculate score (0-100)
    if total_facts > 0:
        score = int((matched_facts / total_facts) * 100)
    else:
        # No ground facts, give neutral score
        score = 50
    
    # Check if recalled memories helped
    recall_helped = False
    if recalled_memories:
        for mem in recalled_memories:
            mem_text = str(mem.get("text", "") or mem.get("query", "") or "").lower()
            if mem_text and any(kw in reply_lower for kw in _extract_keywords(mem_text)[:3]):
                recall_helped = True
                break
    
    reason_parts = []
    if total_facts == 0:
        reason_parts.append("无预设事实")
    else:
        reason_parts.append(f"匹配 {matched_facts}/{total_facts} 事实")
    if recall_helped:
        reason_parts.append("召回记忆有帮助")
    if matched_facts == 0 and total_facts > 0:
        reason_parts.append("回复未包含关键信息")
    
    return {
        "score": score,
        "matched_facts": matched_facts,
        "total_facts": total_facts,
        "recall_helped": recall_helped,
        "reason": "；".join(reason_parts),
        "details": matched_details,
    }


def _extract_keywords(text: str) -> list[str]:
    """Extract keywords from text for matching.
    
    Simple implementation: split by whitespace/punctuation, filter short words.
    """
    import re
    # Split by non-alphanumeric characters (including CJK)
    words = re.split(r'[\s\-_:;,.\'"!?()\[\]{}]+', text.lower())
    # Filter short words and common stopwords
    stopwords = {'的', '是', '在', '有', '和', '了', '与', '对', '这', '那', 'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare', 'ought', 'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during', 'before', 'after', 'above', 'below', 'between', 'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'and', 'but', 'if', 'or', 'because', 'until', 'while', 'it', 'its', 'this', 'that', 'these', 'those', 'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you', 'your', 'yours', 'yourself', 'yourselves', 'he', 'him', 'his', 'himself', 'she', 'her', 'hers', 'herself', 'they', 'them', 'their', 'theirs', 'themselves', 'what', 'which', 'who', 'whom'}
    keywords = [w for w in words if len(w) >= 2 and w not in stopwords]
    return keywords[:10]  # Limit to 10 keywords


def handle_dynamic_eval_delete(
    path: str,
    *,
    send_json,
    get_evaluator,
    remove_evaluator,
) -> bool:
    """Handle DELETE requests for dynamic evaluation endpoints.

    Args:
        path: Request path
        send_json: Function to send JSON response
        get_evaluator: Function to get an evaluator by ID
        remove_evaluator: Function to remove an evaluator

    Returns:
        True if the path was handled, False otherwise
    """
    match = _EVALUATOR_ID_RE.match(path)
    if match:
        evaluator_id = match.group(1)
        evaluator = get_evaluator(evaluator_id)
        if not evaluator:
            send_json({"error": f"Evaluator not found: {evaluator_id}"}, 404)
            return True

        remove_evaluator(evaluator_id)
        send_json({"status": "deleted", "evaluator_id": evaluator_id})
        return True

    return False
