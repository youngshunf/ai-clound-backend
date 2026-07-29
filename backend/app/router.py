"""主路由聚合。

⚡ 冷启动加速（dev 专用，opt-in）：
本模块在 import 时会拉起每个应用模块的整棵 model/schema/crud/service/api 树——30+ 应用累计约
13s，是 `fba run` 热重载「改一次代码 → worker 重启 13s 不可用」的根因。

设环境变量 `FBA_DEV_APPS=hasn_growth,marketplace`（逗号分隔应用名）即可在聚焦后端开发时
**只加载这些应用 + 永远必需的核心（admin/auth）**，把冷启动从 ~13s 压到 ~2-3s。未设置时
（生产 / 默认）行为完全不变：全部应用按原顺序挂载。被裁掉的应用其 REST 端点返回 404——这是
opt-in 的显式取舍，仅用于 dev 聚焦联调。应用名 = 下方 `_APP_LOADERS` 的 key（左列）。
"""

import os

from collections.abc import Callable

from fastapi import APIRouter

# 核心应用 import（登录 / 验证码 / RBAC / 桌面端版本检测 / 登出）——任何 dev 会话都需要，
# 不受白名单裁剪，故置于模块顶部常态加载。
from backend.app.admin.api.router import client as admin_client
from backend.app.admin.api.router import v1 as admin_v1
from backend.app.api.v1.auth import auth_router as client_auth_v1_router

router = APIRouter()

# dev 专用应用白名单。None（未设 env）= 全加载（生产/默认行为不变）；
# 设为集合 = 仅加载白名单 + 核心（admin/auth）。
_DEV_APPS_ENV = os.getenv('FBA_DEV_APPS', '').strip()
_DEV_APPS: set[str] | None = (
    {a.strip() for a in _DEV_APPS_ENV.split(',') if a.strip()} if _DEV_APPS_ENV else None
)


def _want(app_name: str) -> bool:
    """是否挂载某应用模块。None（未设 env）= 全挂；否则仅挂白名单。"""
    return _DEV_APPS is None or app_name in _DEV_APPS


# --------------------------------------------------------------------------- 核心（永远挂载）
router.include_router(admin_v1)
router.include_router(admin_client)           # 桌面端版本检测公开 API
router.include_router(client_auth_v1_router)  # 客户端认证 API (/api/v1/auth/logout 等)


# --------------------------------------------------------------------------- 可裁剪应用（dev 白名单门控）


def _load_task() -> None:
    from backend.app.task.api.router import v1 as task_v1

    router.include_router(task_v1)


def _load_newapi() -> None:
    # 自建 LLM 网关 app/llm 已删除（2026-06-15 new-api 解耦）；/api/v1/llm/* 全部由 app/newapi 接管。
    from backend.app.newapi.api.router import app as newapi_app
    from backend.app.newapi.api.router import v1 as newapi_v1

    router.include_router(newapi_v1)    # /api/v1/llm/*：API Key 管理 + 用户映射 + 用量汇总 + 可用模型目录
    router.include_router(newapi_app)   # new-api 用量与额度（/api/v1/llm/app/newapi）


def _load_hasn_growth() -> None:
    # 获客 canonical 前缀 /api/v1/growth/*（旧 /api/v1/lead-automation/* 薄转发已于 M8 退役 2026-06-13）
    from backend.app.hasn_growth.api.router import agent as growth_agent
    from backend.app.hasn_growth.api.router import app as growth_app
    from backend.app.hasn_growth.api.router import open_api as growth_open
    from backend.app.hasn_growth.api.router import v1 as growth_v1

    router.include_router(growth_v1)
    router.include_router(growth_app)
    router.include_router(growth_agent)
    router.include_router(growth_open)


def _load_billing() -> None:
    # 计费 = 支付 pay + 订阅积分 user_tier 合并（ADR-15 §4）；URL /api/v1/pay 与 /api/v1/user_tier 不变。
    from backend.app.billing.api.router import (
        pay_app,
        pay_open,
        pay_v1,
        user_tier_agent,
        user_tier_app,
        user_tier_open,
        user_tier_v1,
    )

    router.include_router(user_tier_v1)
    router.include_router(user_tier_app)      # 订阅积分-用户端 API
    router.include_router(user_tier_open)     # 订阅积分-公开 API
    router.include_router(user_tier_agent)    # 订阅积分-Agent API
    router.include_router(pay_v1)             # 支付管理 API
    router.include_router(pay_app)            # 支付-用户端 API
    router.include_router(pay_open)           # 支付-公开回调 API


def _load_marketplace() -> None:
    from backend.app.marketplace.api.router import admin as marketplace_admin
    from backend.app.marketplace.api.router import agent as marketplace_agent
    from backend.app.marketplace.api.router import app as marketplace_app
    from backend.app.marketplace.api.router import open_api as marketplace_open
    from backend.app.marketplace.api.router import publish as marketplace_publish
    from backend.app.marketplace.api.router import webhook as marketplace_webhook

    router.include_router(marketplace_publish)  # 发布 API
    router.include_router(marketplace_app)
    router.include_router(marketplace_admin)
    router.include_router(marketplace_open)
    router.include_router(marketplace_agent)
    router.include_router(marketplace_webhook)


def _load_home() -> None:
    # 应用/首页（home 模块，URL /api/v1/hasn/app/{apps,home}/*）
    from backend.app.home.api.router import home_app

    router.include_router(home_app)


def _load_huanxing() -> None:
    from backend.app.huanxing.api.router import agent as huanxing_agent
    from backend.app.huanxing.api.router import app as huanxing_app
    from backend.app.huanxing.api.router import open_api as huanxing_open
    from backend.app.huanxing.api.router import user_api as huanxing_user
    from backend.app.huanxing.api.router import v1 as huanxing_v1

    router.include_router(huanxing_v1)
    router.include_router(huanxing_app)       # 唤星用户端 API
    router.include_router(huanxing_open)      # 唤星公开 API（支付回调等）
    router.include_router(huanxing_agent)     # 唤星Agent API
    router.include_router(huanxing_user)      # 唤星用户级API（Owner Key 认证）


def _load_hasn() -> None:
    # HASN（统一模块，合并原 hasn / hasn_core / hasn_social）
    from backend.app.hasn.api.router import agent as hasn_agent
    from backend.app.hasn.api.router import ai_native as hasn_ai_native
    from backend.app.hasn.api.router import app as hasn_app
    from backend.app.hasn.api.router import artifacts_agent as hasn_artifacts_agent
    from backend.app.hasn.api.router import artifacts_app as hasn_artifacts_app
    from backend.app.hasn.api.router import ci as hasn_ci
    from backend.app.hasn.api.router import open_api as hasn_open
    from backend.app.hasn.api.router import v1 as hasn_v1
    from backend.app.hasn.api.router import ws as hasn_ws

    router.include_router(hasn_v1)            # HASN 管理端 API
    router.include_router(hasn_app)           # HASN 用户端 API
    router.include_router(hasn_agent)         # HASN Agent API
    router.include_router(hasn_open)          # HASN 公开 API
    router.include_router(hasn_ws)            # HASN WebSocket 端点
    router.include_router(hasn_ai_native)        # AI-Native 应用平台 API
    router.include_router(hasn_artifacts_agent)  # 分身产物 Agent API（/api/v1/artifacts/agent）
    router.include_router(hasn_artifacts_app)    # 分身产物 用户端 API（/api/v1/artifacts/app）
    router.include_router(hasn_ci)               # HASN CI 发布面（Bearer 发布密钥·语音目录）


def _load_hasn_community() -> None:
    # HASN 社区（从 hasn 巨型模块拆分的独立模块 hasn_community）
    from backend.app.hasn_community.api.router import admin as community_admin
    from backend.app.hasn_community.api.router import agent as community_agent
    from backend.app.hasn_community.api.router import app as community_app
    from backend.app.hasn_community.api.router import open_api as community_open

    router.include_router(community_app)          # 社区 用户端 API
    router.include_router(community_agent)        # 社区 Agent API（Agent JWT）
    router.include_router(community_open)         # 社区 公开 API（无鉴权只读）
    router.include_router(community_admin)        # 社区 管理端 API（Admin JWT 只读审核）


def _load_hasn_deck() -> None:
    # 演示文稿（自研 PPT 系统，模块 17，独立 PG schema=deck）
    from backend.app.hasn_deck.api.router import agent as deck_agent
    from backend.app.hasn_deck.api.router import app as deck_app

    router.include_router(deck_app)               # 演示文稿 用户端 API（Owner JWT）
    router.include_router(deck_agent)             # 演示文稿 Agent API（Agent JWT，deck:read/write）


def _load_hasn_knowledge() -> None:
    # 知识库（AI-Native 应用 knowledge，独立 PG schema=hasn_knowledge）
    from backend.app.hasn_knowledge.api.router import agent as knowledge_agent
    from backend.app.hasn_knowledge.api.router import app as knowledge_app

    router.include_router(knowledge_app)          # 知识库 用户端 API（Owner JWT）
    router.include_router(knowledge_agent)        # 知识库 Agent API（Agent JWT）


def _load_hasn_task() -> None:
    # 任务系统（AI-Native 应用 hasn_task，模块 12，独立 PG schema=hasn_task）
    from backend.app.hasn_task.api.router import agent as hasn_task_agent
    from backend.app.hasn_task.api.router import app as hasn_task_app

    router.include_router(hasn_task_app)          # 任务系统 用户端 API（Owner JWT + 同步/摘要）
    router.include_router(hasn_task_agent)        # 任务系统 Agent API（Agent JWT，task:read/manage/run）


def _load_hasn_copilot() -> None:
    # 会议副驾（潜行会议副驾，模块 copilot，独立 PG schema=hasn_copilot）数据底座
    from backend.app.hasn_copilot.api.router import app as copilot_app

    router.include_router(copilot_app)            # 会议副驾 用户端 API（Owner JWT，owner 硬隔离）


def _load_hasn_publish() -> None:
    # 通用网页发布与分享（AI-Native 应用 publish，模块 18，独立 PG schema=hasn_publish）
    from backend.app.hasn_publish.api.router import agent as publish_agent
    from backend.app.hasn_publish.api.router import app as publish_app
    from backend.app.hasn_publish.api.router import hosting as publish_hosting
    from backend.app.hasn_publish.api.router import internal as publish_internal
    from backend.app.hasn_publish.api.router import open_meta as publish_open_meta

    router.include_router(publish_app)            # 发布 用户端 API（Owner JWT）
    router.include_router(publish_agent)          # 发布 Agent API（Agent JWT，publish:read/write）
    router.include_router(publish_hosting)        # 发布 公开查看面 /s/{slug}（无鉴权外壳 + CSP sandbox）
    router.include_router(publish_open_meta)      # 发布 公开元数据面（website /s/{slug} 查看器判定态）
    router.include_router(publish_internal)       # Growth 等云端模块调用的收敛内部 HTTP


def _load_notification() -> None:
    # 统一通知服务（§9，单一 emit 入口 + 通知中心权威视图 + 主人偏好）
    from backend.app.notification.api.router import admin as notification_admin
    from backend.app.notification.api.router import agent as notification_agent
    from backend.app.notification.api.router import app as notification_app

    router.include_router(notification_app)        # 通知 用户端 API（Owner JWT）
    router.include_router(notification_agent)      # 通知 Agent API（Agent JWT）
    router.include_router(notification_admin)      # 通知 管理端 API（Admin JWT 只读运维）


def _load_hermes() -> None:
    # Hermes（后台管理 CRUD；用户端 /hermes/app/agents 后续手写编排 API）
    from backend.app.hermes.api.router import app as hermes_app
    from backend.app.hermes.api.router import internal as hermes_internal
    from backend.app.hermes.api.router import v1 as hermes_v1

    router.include_router(hermes_v1)
    router.include_router(hermes_app)
    router.include_router(hermes_internal)    # runtime ↔ backend 内部 service token 调用（X-Internal-Token）


def _load_hasn_client() -> None:
    # 客户端 companion API（hasn_client 应用，URL /api/v1/app/* 兼容；ADR-15 收编 R2）
    from backend.app.hasn_client.api.router import client_router

    router.include_router(client_router)  # 客户端用户端 API (owner_api_keys/push/telemetry/feature-flags)


def _load_hasn_creator() -> None:
    # 创作（hasn_creator，独立 PG schema，URL /api/v1/creator/*）
    from backend.app.hasn_creator.api.router import agent as hasn_creator_agent
    from backend.app.hasn_creator.api.router import app as hasn_creator_app
    from backend.app.hasn_creator.api.router import open_api as hasn_creator_open
    from backend.app.hasn_creator.api.router import v1 as hasn_creator_v1

    router.include_router(hasn_creator_v1)
    router.include_router(hasn_creator_app)
    router.include_router(hasn_creator_agent)
    router.include_router(hasn_creator_open)


def _load_hasn_designsystem() -> None:
    # 设计系统生成（hasn_designsystem，独立 PG schema，URL /api/v1/designsystem/*；当前仅 agent 端）
    from backend.app.hasn_designsystem.api.router import v1 as hasn_designsystem_v1

    router.include_router(hasn_designsystem_v1)


def _load_hasn_plan() -> None:
    # 规划与目标管理（hasn_plan，独立 PG schema，URL /api/v1/plan/*；app+agent 双端）
    from backend.app.hasn_plan.api.router import v1 as hasn_plan_v1

    router.include_router(hasn_plan_v1)


def _load_hasn_finance() -> None:
    # 金融数据（hasn_finance，模块 24）：owner 只读看板面 /api/v1/finance/app/*
    from backend.app.hasn_finance.api.router import app as finance_app

    router.include_router(finance_app)


def _load_hasn_quant() -> None:
    # 量化研究（hasn_quant，模块 14 doc23）：管理端 + owner 业务面（行级隔离回测研究）。
    # codegen 裸 open/agent 面刻意不挂载（公开无鉴权读写=越权大洞；Agent 走云端 MCP 不经 REST）。
    from backend.app.hasn_quant.api.router import app as hasn_quant_app
    from backend.app.hasn_quant.api.router import v1 as hasn_quant_v1

    router.include_router(hasn_quant_v1)
    router.include_router(hasn_quant_app)


def _load_hasn_studio() -> None:
    # 统一视频引擎（hasn_studio，模块 14 doc22）：管理端 + owner 业务面（行级隔离视频工作台）。
    from backend.app.hasn_studio.api.router import app as hasn_studio_app
    from backend.app.hasn_studio.api.router import v1 as hasn_studio_v1

    router.include_router(hasn_studio_v1)
    router.include_router(hasn_studio_app)


def _load_hasn_reel() -> None:
    # 短视频（hasn_reel，模块 14 doc29）：管理端 + owner 业务面（行级隔离项目化创作）。
    from backend.app.hasn_reel.api.router import app as hasn_reel_app
    from backend.app.hasn_reel.api.router import v1 as hasn_reel_v1

    router.include_router(hasn_reel_v1)
    router.include_router(hasn_reel_app)


def _load_hasn_imagelab() -> None:
    # 图坊（hasn_imagelab）：仅保留历史本地引用兼容登记；当前流程直接使用平台项目 UUID。
    # 图坊业务数据在 daemon 本地 SQLite（本地权威），云端这里只保留历史引用兼容表。
    from backend.app.hasn_imagelab.api.router import app as hasn_imagelab_app

    router.include_router(hasn_imagelab_app)


def _load_external_mcp() -> None:
    # 第三方 MCP 网关管理面（external_mcp，doc10/实施99 P7-D）：owner 面 + 平台 admin 面。
    # 刻意不挂 open/agent 裸 CRUD（越权大洞），Agent 经云端 MCP 代理而非 REST 触达 external 工具。
    from backend.app.external_mcp.api.router import admin as external_mcp_admin
    from backend.app.external_mcp.api.router import app as external_mcp_app

    router.include_router(external_mcp_app)
    router.include_router(external_mcp_admin)


def _load_hasn_diag() -> None:
    # 错误诊断与可观测性（hasn_diag，模块 21，独立 PG schema=hasn_diag）：
    # P1 仅 owner 端（错误上行 :sync + owner 只读 /errors）；issue 读/管理走云端 hasn.diag.* MCP 工具。
    from backend.app.hasn_diag.api.router import app as hasn_diag_app

    router.include_router(hasn_diag_app)


def _load_hasn_stock() -> None:
    # 素材站目录（hasn_stock，A-P2，独立 PG schema=hasn_stock）：平台 admin 面 /api/v1/hasn_stock/*。
    # 不挂 open/agent 裸 CRUD（api_key 泄密大洞）；分身经云端 MCP hasn.stock.search/download 触达。
    from backend.app.hasn_stock.api.router import admin as hasn_stock_admin

    router.include_router(hasn_stock_admin)


def _load_hasn_release() -> None:
    # 桌面端发布与自动更新（hasn_release，独立 PG schema=hasn_release）：
    # admin（JWT+RBAC 手动上传/GitHub 构建/版本管理）+ open（官网/下载页/Tauri updater）+ ci（Bearer 回调）。
    # 不挂 agent/app 裸 CRUD——发布是运维动作，分身不经此写库。
    from backend.app.hasn_release.api.router import admin as hasn_release_admin
    from backend.app.hasn_release.api.router import ci as hasn_release_ci
    from backend.app.hasn_release.api.router import open_api as hasn_release_open

    router.include_router(hasn_release_admin)
    router.include_router(hasn_release_open)
    router.include_router(hasn_release_ci)


def _load_hasn_project() -> None:
    # 项目管理 project（schema hasn_project，模块 14 doc38）——平台项目·联邦挂靠一级应用，
    # 只装载 Owner App API。分身经 hasn.project.* MCP 工具调用；不装载 codegen 的 agent/open/v1 CRUD，
    # 避免公开读取、Agent 裸 CRUD 与硬删除重新进入业务路由面。
    from backend.app.hasn_project.api.router import app as hasn_project_app

    router.include_router(hasn_project_app)


# 应用名 → loader。改 FBA_DEV_APPS 时用这里的 key（左列）。
_APP_LOADERS: dict[str, Callable[[], None]] = {
    'task': _load_task,
    'newapi': _load_newapi,
    'hasn_growth': _load_hasn_growth,
    'billing': _load_billing,
    'marketplace': _load_marketplace,
    'home': _load_home,
    'huanxing': _load_huanxing,
    'hasn': _load_hasn,
    'hasn_community': _load_hasn_community,
    'hasn_deck': _load_hasn_deck,
    'hasn_knowledge': _load_hasn_knowledge,
    'hasn_task': _load_hasn_task,
    'hasn_copilot': _load_hasn_copilot,
    'hasn_publish': _load_hasn_publish,
    'notification': _load_notification,
    'hermes': _load_hermes,
    'hasn_client': _load_hasn_client,
    'hasn_creator': _load_hasn_creator,
    'hasn_designsystem': _load_hasn_designsystem,
    'hasn_plan': _load_hasn_plan,
    'hasn_finance': _load_hasn_finance,
    'hasn_quant': _load_hasn_quant,
    'hasn_studio': _load_hasn_studio,
    'hasn_reel': _load_hasn_reel,
    'hasn_imagelab': _load_hasn_imagelab,
    'external_mcp': _load_external_mcp,
    'hasn_diag': _load_hasn_diag,
    'hasn_stock': _load_hasn_stock,
    'hasn_release': _load_hasn_release,
    'hasn_project': _load_hasn_project,
}

for _name, _loader in _APP_LOADERS.items():
    if _want(_name):
        _loader()
