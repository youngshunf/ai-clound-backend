from backend.app.hasn_creator.api.router import v1 as hasn_creator_v1, app as hasn_creator_app, agent as hasn_creator_agent, open_api as hasn_creator_open  # 创作（hasn_creator，独立 PG schema，URL /api/v1/creator/*）
from fastapi import APIRouter

from backend.app.admin.api.router import client as admin_client
from backend.app.admin.api.router import v1 as admin_v1
from backend.app.hasn_client.api.router import client_router
from backend.app.api.v1.auth import auth_router as client_auth_v1_router
from backend.app.hasn_deck.api.router import agent as deck_agent
from backend.app.hasn_deck.api.router import app as deck_app
from backend.app.hasn_knowledge.api.router import agent as knowledge_agent
from backend.app.hasn_knowledge.api.router import app as knowledge_app
from backend.app.hasn_task.api.router import agent as hasn_task_agent
from backend.app.hasn_task.api.router import app as hasn_task_app
from backend.app.hasn_publish.api.router import agent as publish_agent
from backend.app.hasn_publish.api.router import app as publish_app
from backend.app.hasn_publish.api.router import hosting as publish_hosting
from backend.app.hasn.api.router import (
    agent as hasn_agent,
)
from backend.app.hasn.api.router import (
    ai_native as hasn_ai_native,
)
from backend.app.hasn.api.router import (
    app as hasn_app,
)
from backend.app.hasn.api.router import (
    open_api as hasn_open,
)
from backend.app.hasn.api.router import (
    v1 as hasn_v1,
)
from backend.app.hasn.api.router import (
    ws as hasn_ws,
)
from backend.app.hasn.api.router import (
    artifacts_agent as hasn_artifacts_agent,
)
from backend.app.hasn.api.router import (
    artifacts_app as hasn_artifacts_app,
)
from backend.app.hasn_community.api.router import admin as community_admin
from backend.app.hasn_community.api.router import agent as community_agent
from backend.app.hasn_community.api.router import app as community_app
from backend.app.hasn_community.api.router import open_api as community_open
from backend.app.hermes.api.router import app as hermes_app
from backend.app.hermes.api.router import internal as hermes_internal
from backend.app.hermes.api.router import v1 as hermes_v1
from backend.app.huanxing.api.router import agent as huanxing_agent
from backend.app.huanxing.api.router import app as huanxing_app
from backend.app.huanxing.api.router import open_api as huanxing_open
from backend.app.huanxing.api.router import user_api as huanxing_user
from backend.app.huanxing.api.router import v1 as huanxing_v1
# 获客（hasn_growth，采集子域收编，独立 PG schema=hasn_growth）
# canonical 前缀 /api/v1/growth/*（旧 /api/v1/lead-automation/* 薄转发已于 M8 退役 2026-06-13）
from backend.app.hasn_growth.api.router import (
    agent as growth_agent,
)
from backend.app.hasn_growth.api.router import (
    app as growth_app,
)
from backend.app.hasn_growth.api.router import (
    open_api as growth_open,
)
from backend.app.hasn_growth.api.router import (
    v1 as growth_v1,
)
# 自建 LLM 网关 app/llm 已删除（2026-06-15 new-api 解耦）；/api/v1/llm/* 全部由 app/newapi 接管。
# new-api 集成模块（D1/D5）：自建 API Key + new-api 用户映射 + 用量汇总 + 可用模型目录。URL 前缀 /api/v1/llm/* 不变。
from backend.app.newapi.api.router import app as newapi_app
from backend.app.newapi.api.router import v1 as newapi_v1
from backend.app.marketplace.api.router import admin as marketplace_admin
from backend.app.marketplace.api.router import agent as marketplace_agent
from backend.app.marketplace.api.router import app as marketplace_app
from backend.app.marketplace.api.router import open_api as marketplace_open
from backend.app.marketplace.api.router import publish as marketplace_publish
from backend.app.marketplace.api.router import webhook as marketplace_webhook
from backend.app.notification.api.router import admin as notification_admin
from backend.app.notification.api.router import agent as notification_agent
from backend.app.notification.api.router import app as notification_app
# 计费（billing）= 支付 pay + 订阅积分 user_tier 合并（ADR-15 §4）；URL 前缀 /api/v1/pay 与 /api/v1/user_tier 保持不变
from backend.app.billing.api.router import pay_v1
from backend.app.billing.api.router import pay_app
from backend.app.billing.api.router import pay_open
from backend.app.billing.api.router import user_tier_v1
from backend.app.billing.api.router import user_tier_app
from backend.app.billing.api.router import user_tier_open
from backend.app.billing.api.router import user_tier_agent
from backend.app.task.api.router import v1 as task_v1
# 工作台（workbench）= 原 app/hasn 工作台子域按 ADR-15 §4 抽出；URL /api/v1/hasn/app/workbench/* 保持不变
from backend.app.workbench.api.router import workbench_app

router = APIRouter()

router.include_router(admin_v1)
router.include_router(task_v1)
router.include_router(newapi_v1)    # /api/v1/llm/*：API Key 管理 + 用户映射 + 用量汇总 + 可用模型目录（接管原 app/llm 存活路径）
router.include_router(newapi_app)   # new-api 用量与额度（/api/v1/llm/app/newapi）
# 获客 canonical 前缀 /api/v1/growth/*（旧 /api/v1/lead-automation/* 薄转发已于 M8 退役 2026-06-13）
router.include_router(growth_v1)
router.include_router(growth_app)
router.include_router(growth_agent)
router.include_router(growth_open)

router.include_router(user_tier_v1)
router.include_router(user_tier_app)      # 订阅积分-用户端 API
router.include_router(user_tier_open)     # 订阅积分-公开 API
router.include_router(user_tier_agent)    # 订阅积分-Agent API
router.include_router(marketplace_publish)  # 发布 API
router.include_router(admin_client)       # 桌面端版本检测公开 API

# 支付（独立模块）
router.include_router(pay_v1)             # 支付管理 API
router.include_router(pay_app)            # 支付-用户端 API
router.include_router(pay_open)           # 支付-公开回调 API

# 工作台（workbench 应用，URL /api/v1/hasn/app/workbench/*）
router.include_router(workbench_app)

# 唤星
router.include_router(huanxing_v1)
router.include_router(huanxing_app)       # 唤星用户端 API
router.include_router(huanxing_open)      # 唤星公开 API（支付回调等）
router.include_router(huanxing_agent)     # 唤星Agent API
router.include_router(huanxing_user)      # 唤星用户级API（Owner Key 认证）

# HASN（统一模块，合并原 hasn / hasn_core / hasn_social）
router.include_router(hasn_v1)            # HASN 管理端 API
router.include_router(hasn_app)           # HASN 用户端 API
router.include_router(hasn_agent)         # HASN Agent API
router.include_router(hasn_open)          # HASN 公开 API
router.include_router(hasn_ws)            # HASN WebSocket 端点
router.include_router(hasn_ai_native)      # AI-Native 应用平台 API
router.include_router(hasn_artifacts_agent)  # 分身产物 Agent API（/api/v1/artifacts/agent，Agent JWT）
router.include_router(hasn_artifacts_app)    # 分身产物 用户端 API（/api/v1/artifacts/app，Owner JWT）

# HASN 社区（从 hasn 巨型模块拆分的独立模块 hasn_community）
router.include_router(community_app)          # 社区 用户端 API（/api/v1/community/app）
router.include_router(community_agent)        # 社区 Agent API（/api/v1/community/agent，Agent JWT）
router.include_router(community_open)         # 社区 公开 API（/api/v1/community/open，无鉴权只读）
router.include_router(community_admin)        # 社区 管理端 API（/api/v1/community/admin，Admin JWT 只读审核）

# 演示文稿（自研 PPT 系统，模块 17，独立 PG schema=deck）
router.include_router(deck_app)               # 演示文稿 用户端 API（/api/v1/deck/app，Owner JWT，owner 隔离）
router.include_router(deck_agent)             # 演示文稿 Agent API（/api/v1/deck/agent，Agent JWT，deck:read/write）

# 知识库（AI-Native 应用 knowledge，独立 PG schema=hasn_knowledge，RAGFlow 为内部处理后端）
router.include_router(knowledge_app)          # 知识库 用户端 API（/api/v1/knowledge/app，Owner JWT，owner 隔离）
router.include_router(knowledge_agent)        # 知识库 Agent API（/api/v1/knowledge/agent，Agent JWT，knowledge:read/upload/write）

# 任务系统（AI-Native 应用 hasn_task，模块 12，独立 PG schema=hasn_task）
router.include_router(hasn_task_app)          # 任务系统 用户端 API（/api/v1/hasn-task/app，Owner JWT + 同步/摘要）
router.include_router(hasn_task_agent)        # 任务系统 Agent API（/api/v1/hasn-task/agent，Agent JWT，task:read/manage/run）

# 通用网页发布与分享（AI-Native 应用 publish，模块 18，独立 PG schema=hasn_publish）
router.include_router(publish_app)            # 发布 用户端 API（/api/v1/publish/app，Owner JWT，owner 隔离）
router.include_router(publish_agent)          # 发布 Agent API（/api/v1/publish/agent，Agent JWT，publish:read/write）
router.include_router(publish_hosting)        # 发布 公开查看面 /s/{slug}（独立分享域名，无鉴权外壳 + CSP sandbox）

# 统一通知服务（§9，单一 emit 入口 + 通知中心权威视图 + 主人偏好）
router.include_router(notification_app)        # 通知 用户端 API（/api/v1/notifications/app，Owner JWT）
router.include_router(notification_agent)      # 通知 Agent API（/api/v1/notifications/agent，Agent JWT）
router.include_router(notification_admin)      # 通知 管理端 API（/api/v1/notifications/admin，Admin JWT 只读运维）


# Hermes（后台管理 CRUD；用户端 /hermes/app/agents 后续手写编排 API）
router.include_router(hermes_v1)
router.include_router(hermes_app)
router.include_router(hermes_internal)    # runtime ↔ backend 内部 service token 调用（X-Internal-Token）

# 客户端 companion API（hasn_client 应用，URL /api/v1/app/* 兼容；ADR-15 收编 R2）
router.include_router(client_router)  # 客户端用户端 API (owner_api_keys/push/telemetry/feature-flags)
router.include_router(client_auth_v1_router)  # 客户端认证 API (/api/v1/auth/logout 等；auth 暂留平台底座位置)
router.include_router(marketplace_app)
router.include_router(marketplace_admin)
router.include_router(marketplace_open)
router.include_router(marketplace_agent)
router.include_router(marketplace_webhook)

router.include_router(hasn_creator_v1)
router.include_router(hasn_creator_app)
router.include_router(hasn_creator_agent)
router.include_router(hasn_creator_open)
