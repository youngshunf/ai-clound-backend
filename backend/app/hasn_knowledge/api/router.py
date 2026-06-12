"""知识库（AI-Native 应用 knowledge）模块路由聚合。

- /api/v1/knowledge/app/*    用户端（Owner JWT，owner 隔离；daemon 代理为 /api/v1/knowledge/*）
- /api/v1/knowledge/agent/*  Agent 端（Agent JWT，维度①scope + 维度②grant + owner 隔离）

注：知识库为私有用户内容，不开 open scope；admin 后置（设计 §2.1）。
URL 前缀不带 hasn_ 前缀语义（同 hasn_community ↔ /api/v1/community/* 先例）。
"""

from fastapi import APIRouter

from backend.app.hasn_knowledge.api.v1.agent.knowledge import router as knowledge_agent_router
from backend.app.hasn_knowledge.api.v1.app.knowledge import router as knowledge_app_router
from backend.core.conf import settings

app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/knowledge/app', tags=['知识库-用户端'])
app.include_router(knowledge_app_router)

agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/knowledge/agent', tags=['知识库-Agent端'])
agent.include_router(knowledge_agent_router)
