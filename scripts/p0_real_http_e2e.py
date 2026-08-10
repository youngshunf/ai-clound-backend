#!/usr/bin/env python3
"""Run the HASN P0 cloud-backend HTTP E2E flow on a real local socket.

The app uses the same P0 FastAPI routers as production. Test gateways are
in-memory so the script is repeatable without production secrets or a shared DB,
but every step below is sent through an actual uvicorn HTTP listener.
"""

from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / ".omx" / "ralph" / "p0-real-http-e2e-evidence.json"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from backend.tests.hasn.test_p0_real_http_e2e import make_app

    port = find_free_port()
    monkeypatch = pytest.MonkeyPatch()
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    evidence: dict[str, Any] = {
        "started_at": utc_now(),
        "ports": {"backend": port},
        "checks": [],
    }

    try:
        app = make_app(monkeypatch)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="hasn-p0-backend-http", daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{port}"
        wait_for_http(base_url, evidence)
        run_flow(base_url, evidence)
        evidence["completed_at"] = utc_now()
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5)
        monkeypatch.undo()


def run_flow(base_url: str, evidence: dict[str, Any]) -> None:
    send = request_json(base_url, "/api/v1/hasn/auth/phone/send_code", method="POST", payload={"phone": "13800138000"})
    evidence["checks"].append({"name": "backend auth phone send_code", "sent": True, "response": send})

    verify = request_json(
        base_url,
        "/api/v1/hasn/auth/phone/verify",
        method="POST",
        payload={"phone": "13800138000", "code": "123456", "pending_intent_id": "pi_p0_real"},
    )
    token = verify["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    evidence["checks"].append({"name": "backend auth phone verify", "token_prefix": token[:12]})

    onboarding = request_json(
        base_url,
        "/api/v1/hasn/onboarding/ensure",
        method="POST",
        headers=auth,
        payload={
            "node": {
                "node_id": "n_p0_desktop",
                "device_name": "P0 Desktop",
                "platform": "macos",
                "client_version": "p0-real-http-script",
            },
            "client": {"protocol": "hasn/0.2", "supported_extensions": ["sync.pull", "message_hub"]},
            "pending_intent_id": "pi_p0_real",
        },
    )
    assert onboarding["human"]["owner_id"] == "h_p0_owner"
    assert onboarding["default_agent"]["hasn_id"] == "a_p0_default"
    agent_token = onboarding["default_agent"]["access_token"]
    agent_auth = {"Authorization": f"Bearer {agent_token}"}
    evidence["checks"].append(
        {
            "name": "backend onboarding ensure",
            "owner_id": onboarding["human"]["owner_id"],
            "default_agent": onboarding["default_agent"]["hasn_id"],
            "sandbox_status": onboarding["sandbox"]["status"],
            "agent_token_prefix": agent_token[:12],
        }
    )

    # Runtime 上报写入端点已随云端 Runtime 形态退役摘除（2026-08-10）。这里走真实 socket
    # 断言它确实 404——不是「跳过不测」，而是把「端点必须不在」当成一条正向证据落进 evidence。
    runtime_report_status = request_status(
        base_url,
        "/api/v1/hasn/runtime/report",
        method="POST",
        headers=auth,
        payload={
            "owner_id": "h_p0_owner",
            "node_id": "n_p0_desktop",
            "runtime_summaries": [
                {
                    "agent_id": "a_p0_default",
                    "binding_id": "bind_p0_default",
                    "runtime_type": "hermes",
                    "status": "online",
                    "adapter_registered": True,
                    "handle_available": True,
                    "summary_json": {"capability": "dispatch"},
                }
            ],
        },
    )
    assert runtime_report_status == 404, f"runtime/report 应已退役返回 404，实得 {runtime_report_status}"
    evidence["checks"].append({"name": "backend runtime report retired", "status": runtime_report_status})

    sync_push = request_json(
        base_url,
        "/api/v1/hasn/sync/push",
        method="POST",
        headers=auth,
        payload={
            "owner_id": "h_p0_owner",
            "node_id": "n_p0_desktop",
            "events": [{"client_event_id": "ce_1", "event_type": "node.session", "payload": {"status": "ready"}}],
        },
    )
    assert sync_push["accepted"] == 1
    sync_pull = request_json(
        base_url,
        "/api/v1/hasn/sync/pull",
        method="POST",
        headers=auth,
        payload={"owner_id": "h_p0_owner", "cursor": "owner:h_p0_owner:0", "limit": 20},
    )
    evidence["checks"].append(
        {
            "name": "backend sync push/pull",
            "accepted": sync_push["accepted"],
            "pulled_events": [event["event_type"] for event in sync_pull["events"]],
        }
    )

    human_message = request_json(
        base_url,
        "/api/v1/hasn/messages/send",
        method="POST",
        headers=auth,
        payload={
            "owner_id": "h_p0_owner",
            "envelope": {"conversation_id": "00000000-0000-0000-0000-000000000201", "to_id": "h_p0_owner"},
        },
    )
    agent_message = request_json(
        base_url,
        "/api/v1/hasn/messages/send",
        method="POST",
        headers=auth,
        payload={
            "owner_id": "h_sender",
            "envelope": {"conversation_id": "00000000-0000-0000-0000-000000000202", "to_id": "a_p0_default"},
        },
    )
    assert human_message["dispatch_status"] == "not_required"
    assert agent_message["delivery_status"] == "delivered"
    assert agent_message["dispatch_status"] == "dispatch_failed"
    assert agent_message["suppressed_inbox_created"] is True
    evidence["checks"].append(
        {
            "name": "backend message delivery and runtime-unavailable semantics",
            "human_dispatch_status": human_message["dispatch_status"],
            "agent_dispatch_status": agent_message["dispatch_status"],
            "suppressed_inbox_created": agent_message["suppressed_inbox_created"],
            "warnings": [warning["name"] for warning in agent_message["warnings"]],
        }
    )

    inbox = request_json(
        base_url,
        "/api/v1/hasn/inbox/pull",
        method="POST",
        headers=auth,
        payload={"owner_id": "h_p0_owner", "include_suppressed": True},
    )
    inbox_kinds = [item["inbox_kind"] for item in inbox["items"]]
    assert {"human_inbox", "agent_inbox", "owner_copy", "suppressed_inbox"}.issubset(set(inbox_kinds))
    evidence["checks"].append({"name": "backend inbox pull", "inbox_kinds": inbox_kinds, "next_cursor": inbox["next_cursor"]})

    capabilities = request_json(
        base_url,
        "/api/v1/ai-native/runtime/capabilities",
        method="POST",
        headers=agent_auth,
        payload={"workspace": None, "include_disabled": False, "trace_id": "trace-cap-personal"},
    )["data"]
    assert capabilities["workspace"]["workspace_key"] == "personal:7"
    assert capabilities["tools"][0]["tool_id"] == "knowledge.search"
    evidence["checks"].append(
        {
            "name": "backend ai-native personal capabilities",
            "workspace": capabilities["workspace"],
            "tools": [tool["mcp_name"] for tool in capabilities["tools"]],
        }
    )

    enterprise_capabilities = request_json(
        base_url,
        "/api/v1/ai-native/runtime/capabilities",
        method="POST",
        headers=agent_auth,
        payload={
            "workspace": {"kind": "enterprise", "enterprise_id": 42},
            "include_disabled": False,
            "trace_id": "trace-cap-enterprise",
        },
    )["data"]
    assert enterprise_capabilities["workspace"]["workspace_key"] == "enterprise:42"
    evidence["checks"].append(
        {
            "name": "backend ai-native enterprise capabilities",
            "workspace": enterprise_capabilities["workspace"],
            "tools": [tool["tool_id"] for tool in enterprise_capabilities["tools"]],
        }
    )

    tool_call = request_json(
        base_url,
        "/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call",
        method="POST",
        headers=agent_auth,
        payload={
            "workspace": {"kind": "enterprise", "enterprise_id": 42},
            "input": {"query": "唤星工作台", "limit": 10},
            "trace_id": "trace-tool-enterprise",
        },
    )["data"]
    assert tool_call["decision"] == "allow"
    assert tool_call["workspace"]["workspace_key"] == "enterprise:42"
    evidence["checks"].append(
        {
            "name": "backend ai-native knowledge.search tool call",
            "decision": tool_call["decision"],
            "workspace": tool_call["workspace"],
            "result_total": tool_call["result"]["total"],
            "audit_id": tool_call["audit_id"],
        }
    )

    audit = request_json(
        base_url,
        "/api/v1/ai-native/audit?app_id=knowledge&agent_hasn_id=a_p0_default&trace_id=trace-tool-enterprise",
    )["data"]
    assert audit["total"] == 1
    evidence["checks"].append(
        {
            "name": "backend ai-native audit retrieval",
            "total": audit["total"],
            "tool_id": audit["items"][0]["tool_id"],
            "decision": audit["items"][0]["decision"],
        }
    )

    invalid_input = request_json(
        base_url,
        "/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call",
        method="POST",
        headers=agent_auth,
        payload={
            "workspace": {"kind": "enterprise", "enterprise_id": 42},
            "input": {"query": "", "limit": 0},
            "trace_id": "trace-tool-invalid-input",
        },
    )["data"]
    assert invalid_input["decision"] == "deny"
    assert invalid_input["error"] == {"code": "15020", "message": "input_schema_invalid"}

    invalid_input_audit = request_json(
        base_url,
        "/api/v1/ai-native/audit?app_id=knowledge&agent_hasn_id=a_p0_default&trace_id=trace-tool-invalid-input",
    )["data"]
    assert invalid_input_audit["total"] == 1
    evidence["checks"].append(
        {
            "name": "backend ai-native invalid input audit",
            "decision": invalid_input["decision"],
            "error": invalid_input["error"],
            "audit_total": invalid_input_audit["total"],
        }
    )


def wait_for_http(base_url: str, evidence: dict[str, Any]) -> None:
    deadline = time.time() + 20
    last_error: str | None = None
    while time.time() < deadline:
        try:
            openapi = request_json(base_url, "/openapi.json")
            evidence["checks"].append({"name": "backend uvicorn openapi", "paths": len(openapi.get("paths", {}))})
            return
        except (OSError, URLError, HTTPError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.2)
    raise RuntimeError(f"backend HTTP server did not become ready: {last_error}")


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = Request(f"{base_url}{path}", data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {error.code} {detail}") from error


def request_status(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> int:
    """只取 HTTP 状态码（用于断言某端点已退役），4xx/5xx 不抛异常。"""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = Request(f"{base_url}{path}", data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return int(response.status)
    except HTTPError as error:
        return int(error.code)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    raise SystemExit(main())
