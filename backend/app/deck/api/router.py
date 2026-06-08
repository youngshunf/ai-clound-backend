"""演示文稿（模块 17）模块路由聚合。

- /api/v1/deck/app/*    用户端（Owner JWT，owner 隔离）
- /api/v1/deck/agent/*  Agent 端（Agent JWT，owner=agent.owner_hasn_id，scope 闸 deck:read/deck:write）

注：deck 为私有用户内容，不开 open/admin scope。
"""

from fastapi import APIRouter

from backend.app.deck.api.v1.agent.deck import router as deck_agent_router
from backend.app.deck.api.v1.app.deck import router as deck_app_router
from backend.core.conf import settings

app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/deck/app', tags=['演示文稿-用户端'])
app.include_router(deck_app_router)

agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/deck/agent', tags=['演示文稿-Agent端'])
agent.include_router(deck_agent_router)
