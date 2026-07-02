from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.agent import hasn_agents as agent_hasn_agents_api
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.exception_handler import register_exception
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import get_db, get_db_transaction


class FakeDbSession:
    pass


@pytest.fixture
def agent_heartbeat_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=True)])
    register_exception(app)
    app.include_router(agent_hasn_agents_api.router, prefix="/api/v1/hasn/agent/agents")

    async def fake_db() -> FakeDbSession:
        return FakeDbSession()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_db_transaction] = fake_db

    return app


def test_agent_heartbeat_route_requires_agent_jwt(agent_heartbeat_app: FastAPI) -> None:
    with TestClient(agent_heartbeat_app) as client:
        response = client.post(
            "/api/v1/hasn/agent/agents/by-hasn-id/a_agent/heartbeat",
            json={
                "node_id": "n_node",
                "online_status": "online",
                "health_status": "ok",
                "last_heartbeat_at": 1_700_000_000,
            },
        )

    assert response.status_code == 401


def test_agent_heartbeat_route_reports_only_authenticated_agent(
    agent_heartbeat_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_agent_auth(request: Request) -> None:
        request.state.agent = AgentTokenPayload(
            agent_hasn_id="a_agent",
            agent_name="Agent",
            owner_hasn_id="h_owner",
            owner_user_id=7,
            session_uuid="session-001",
            expire_time=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

    async def fake_update_heartbeat(
        db: FakeDbSession, *, hasn_id: str, request: Any, user_id: int | None
    ):
        calls.append(
            {
                "db": db,
                "hasn_id": hasn_id,
                "node_id": request.node_id,
                "online_status": request.online_status,
                "health_status": request.health_status,
                "last_heartbeat_at": request.last_heartbeat_at,
                "user_id": user_id,
            }
        )
        return SimpleNamespace(success=True)

    monkeypatch.setattr(
        agent_hasn_agents_api.agent_profile_service,
        "update_heartbeat",
        fake_update_heartbeat,
    )
    agent_heartbeat_app.dependency_overrides[
        DependsAgentJwtAuth.dependency
    ] = fake_agent_auth

    with TestClient(agent_heartbeat_app) as client:
        response = client.post(
            "/api/v1/hasn/agent/agents/by-hasn-id/a_agent/heartbeat",
            json={
                "node_id": "n_node",
                "online_status": "online",
                "health_status": "ok",
                "last_heartbeat_at": 1_700_000_000,
            },
        )
        forbidden = client.post(
            "/api/v1/hasn/agent/agents/by-hasn-id/a_other/heartbeat",
            json={
                "node_id": "n_node",
                "online_status": "online",
                "health_status": "ok",
                "last_heartbeat_at": 1_700_000_001,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"success": True}
    assert forbidden.status_code == 403
    assert calls == [
        {
            "db": calls[0]["db"],
            "hasn_id": "a_agent",
            "node_id": "n_node",
            "online_status": "online",
            "health_status": "ok",
            "last_heartbeat_at": 1_700_000_000,
            "user_id": 7,
        }
    ]
