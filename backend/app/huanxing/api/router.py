from fastapi import APIRouter

# --- admin/ 管理端（JWT + RBAC）---
from backend.app.huanxing.api.v1.admin.analytics import router as admin_analytics_router

# 服务器/用户登记子域（huanxing_server / huanxing_user 两表）已于 2026-06-16 整体退役：
# 桌面端（hasn-node）零引用、外部消费方为空，属 HASN 化之前的旧 sidecar 登记机制。
# 删除 model/schema/crud/service + admin server/user/dashboard + agent server/dashboard/
# user_sync/user_check 全套，两表经 sql/huanxing/migrations 迁移 DROP。
#
# 文档/目录/分享子域已随知识库 AI-Native 重做整体退役（D9 吸收为知识库原生文档，
# 设计《知识库AI-Native应用重设计（RAGFlow处理后端）》§7.1；存量经
# scripts/migrate_huanxing_docs_to_knowledge.py 迁移）。
# --- open/ 公开（无需认证）---
# --- agent/ Agent端（X-Agent-Key）---
from backend.app.huanxing.api.v1.agent.file import router as agent_file_router
from backend.app.huanxing.api.v1.agent.website import router as agent_website_router

# --- user/ 用户级（Owner Key 认证）---
from backend.app.huanxing.api.v1.user.file import router as user_file_router
from backend.app.huanxing.api.v1.user.website import router as user_website_router
from backend.core.conf import settings

# ========================================
# 管理端 API（JWT + RBAC）
# 路径前缀: /api/v1/huanxing/
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/huanxing', tags=['唤星管理'])

v1.include_router(admin_analytics_router, prefix='/analytics', tags=['唤星管理-分析看板'])

# ========================================
# 用户端 API（仅 JWT，无 RBAC）
# 路径前缀: /api/v1/huanxing/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/huanxing/app', tags=['唤星用户端'])


# ========================================
# 公开 API（无需认证）
# 路径前缀: /api/v1/huanxing/open/
# ========================================
open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/huanxing/open', tags=['唤星公开'])


# ========================================
# Agent API（X-Agent-Key 认证，兼容期保留）
# 路径前缀: /api/v1/huanxing/agent/
# ========================================
agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/huanxing/agent', tags=['唤星Agent'])

agent.include_router(agent_file_router, prefix='/files', tags=['唤星Agent-文件'])
agent.include_router(agent_website_router, prefix='/website', tags=['唤星Agent-网站部署'])

# ========================================
# 用户级 API（Owner Key 认证，SDK / 桌面端 / Agent 统一入口）
# 路径前缀: /api/v1/huanxing/user/
# ========================================
user_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/huanxing/user', tags=['唤星用户级API'])

user_api.include_router(user_file_router, prefix='/files', tags=['唤星用户级-文件'])
user_api.include_router(user_website_router, prefix='/website', tags=['唤星用户级-网站部署'])
