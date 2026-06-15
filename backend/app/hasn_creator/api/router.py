from fastapi import APIRouter
from fastapi.routing import APIRoute

from backend.core.conf import settings

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn_creator.api.v1.admin.project import router as admin_project_router
from backend.app.hasn_creator.api.v1.admin.playbook import router as admin_playbook_router
from backend.app.hasn_creator.api.v1.admin.profile import router as admin_profile_router
from backend.app.hasn_creator.api.v1.admin.account import router as admin_account_router
from backend.app.hasn_creator.api.v1.admin.competitor import router as admin_competitor_router
from backend.app.hasn_creator.api.v1.admin.content import router as admin_content_router
from backend.app.hasn_creator.api.v1.admin.content_stage import router as admin_content_stage_router
from backend.app.hasn_creator.api.v1.admin.topic import router as admin_topic_router
from backend.app.hasn_creator.api.v1.admin.draft import router as admin_draft_router
from backend.app.hasn_creator.api.v1.admin.publish import router as admin_publish_router
from backend.app.hasn_creator.api.v1.admin.media import router as admin_media_router
from backend.app.hasn_creator.api.v1.admin.content_insight import router as admin_content_insight_router
from backend.app.hasn_creator.api.v1.admin.viral_pattern import router as admin_viral_pattern_router
from backend.app.hasn_creator.api.v1.admin.hot_topic import router as admin_hot_topic_router
# --- 用户端（仅 JWT）：owner 业务面（包裹 creator_service，企业化裁剪），替代 codegen 裸 CRUD ---
# WebUI 经 daemon /api/v1/creator/* 薄代理调用 /api/v1/creator/app/*（铁律）。
from backend.app.hasn_creator.api.v1.app.creator import router as app_creator_router
# --- Agent（Agent Key） ---
from backend.app.hasn_creator.api.v1.agent.project import router as agent_project_router
from backend.app.hasn_creator.api.v1.agent.playbook import router as agent_playbook_router
from backend.app.hasn_creator.api.v1.agent.profile import router as agent_profile_router
from backend.app.hasn_creator.api.v1.agent.account import router as agent_account_router
from backend.app.hasn_creator.api.v1.agent.competitor import router as agent_competitor_router
from backend.app.hasn_creator.api.v1.agent.content import router as agent_content_router
from backend.app.hasn_creator.api.v1.agent.content_stage import router as agent_content_stage_router
from backend.app.hasn_creator.api.v1.agent.topic import router as agent_topic_router
from backend.app.hasn_creator.api.v1.agent.draft import router as agent_draft_router
from backend.app.hasn_creator.api.v1.agent.publish import router as agent_publish_router
from backend.app.hasn_creator.api.v1.agent.media import router as agent_media_router
from backend.app.hasn_creator.api.v1.agent.content_insight import router as agent_content_insight_router
from backend.app.hasn_creator.api.v1.agent.viral_pattern import router as agent_viral_pattern_router
from backend.app.hasn_creator.api.v1.agent.hot_topic import router as agent_hot_topic_router
# --- 公开（无需认证） ---
from backend.app.hasn_creator.api.v1.open.project import router as open_project_router
from backend.app.hasn_creator.api.v1.open.playbook import router as open_playbook_router
from backend.app.hasn_creator.api.v1.open.profile import router as open_profile_router
from backend.app.hasn_creator.api.v1.open.account import router as open_account_router
from backend.app.hasn_creator.api.v1.open.competitor import router as open_competitor_router
from backend.app.hasn_creator.api.v1.open.content import router as open_content_router
from backend.app.hasn_creator.api.v1.open.content_stage import router as open_content_stage_router
from backend.app.hasn_creator.api.v1.open.topic import router as open_topic_router
from backend.app.hasn_creator.api.v1.open.draft import router as open_draft_router
from backend.app.hasn_creator.api.v1.open.publish import router as open_publish_router
from backend.app.hasn_creator.api.v1.open.media import router as open_media_router
from backend.app.hasn_creator.api.v1.open.content_insight import router as open_content_insight_router
from backend.app.hasn_creator.api.v1.open.viral_pattern import router as open_viral_pattern_router
from backend.app.hasn_creator.api.v1.open.hot_topic import router as open_hot_topic_router

# ========================================
# 管理端 API（JWT + RBAC）
# 路径前缀: /api/v1/hasn_creator/
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/creator', tags=['运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度管理'])

v1.include_router(admin_project_router, prefix='/project', tags=['运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度管理-运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度'])
v1.include_router(admin_playbook_router, prefix='/playbooks', tags=['获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义-获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义'])
v1.include_router(admin_profile_router, prefix='/profiles', tags=['项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）-项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）'])
v1.include_router(admin_account_router, prefix='/accounts', tags=['平台账号（1:N project）；同一项目多平台真实账号-平台账号（1:N project）；同一项目多平台真实账号'])
v1.include_router(admin_competitor_router, prefix='/competitors', tags=['竞品账号（定位/选题调研输入）-竞品账号（定位/选题调研输入）'])
v1.include_router(admin_content_router, prefix='/contents', tags=['内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核-内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核'])
v1.include_router(admin_content_stage_router, prefix='/content/stages', tags=['阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播-阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播'])
v1.include_router(admin_topic_router, prefix='/topics', tags=['选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过-选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过'])
v1.include_router(admin_draft_router, prefix='/drafts', tags=['草稿箱（灵感快速捕获，轻量独立于正式流水线）-草稿箱（灵感快速捕获，轻量独立于正式流水线）'])
v1.include_router(admin_publish_router, prefix='/publishs', tags=['发布记录（= content × account：发到某平台账号 + 数据指标）-发布记录（= content × account：发到某平台账号 + 数据指标）'])
v1.include_router(admin_media_router, prefix='/medias', tags=['素材库；配图/封面/视频/模板（私有桶引用）-素材库；配图/封面/视频/模板（私有桶引用）'])
v1.include_router(admin_content_insight_router, prefix='/content/insights', tags=['内容洞察（复盘结构化结论，进化沉淀核心）-内容洞察（复盘结构化结论，进化沉淀核心）'])
v1.include_router(admin_viral_pattern_router, prefix='/viral/patterns', tags=['爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）-爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）'])
v1.include_router(admin_hot_topic_router, prefix='/hot/topics', tags=['热榜快照（全局，去重，喂选题；可选数据源）-热榜快照（全局，去重，喂选题；可选数据源）'])

def _prefix_route_names(router: APIRouter, prefix: str) -> APIRouter:
    """给 router 内全部路由名加前缀，保证 route name 全局唯一（FastAPI route.name 默认取函数名且
    全局唯一与挂载 prefix 无关；详见 utils.openapi.ensure_unique_route_names）。"""
    for route in router.routes:
        if isinstance(route, APIRoute):
            route.name = f'{prefix}{route.name}'
            route.operation_id = route.name
    return router


# ========================================
# 用户端 API（仅 JWT，无 RBAC）
# 路径前缀: /api/v1/creator/app/
# owner 面 = 业务操作（包裹 creator_service + CreatorScope 企业化裁剪），替代 codegen 裸 CRUD；
# WebUI 经 daemon /api/v1/creator/* 薄代理调用本面（铁律）。Agent 面走云端 MCP（不在此）。
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/creator/app', tags=['创作运营-用户端业务面'])

app.include_router(_prefix_route_names(app_creator_router, 'creator_app_'), tags=['创作运营-用户端业务面'])

# ========================================
# 公开 API（无需认证）
# 路径前缀: /api/v1/hasn_creator/open/
# ========================================
open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/creator/open', tags=['运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度公开'])

open_api.include_router(open_project_router, prefix='/project', tags=['运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度公开-运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度'])
open_api.include_router(open_playbook_router, prefix='/playbooks', tags=['获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义-获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义'])
open_api.include_router(open_profile_router, prefix='/profiles', tags=['项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）-项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）'])
open_api.include_router(open_account_router, prefix='/accounts', tags=['平台账号（1:N project）；同一项目多平台真实账号-平台账号（1:N project）；同一项目多平台真实账号'])
open_api.include_router(open_competitor_router, prefix='/competitors', tags=['竞品账号（定位/选题调研输入）-竞品账号（定位/选题调研输入）'])
open_api.include_router(open_content_router, prefix='/contents', tags=['内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核-内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核'])
open_api.include_router(open_content_stage_router, prefix='/content/stages', tags=['阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播-阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播'])
open_api.include_router(open_topic_router, prefix='/topics', tags=['选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过-选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过'])
open_api.include_router(open_draft_router, prefix='/drafts', tags=['草稿箱（灵感快速捕获，轻量独立于正式流水线）-草稿箱（灵感快速捕获，轻量独立于正式流水线）'])
open_api.include_router(open_publish_router, prefix='/publishs', tags=['发布记录（= content × account：发到某平台账号 + 数据指标）-发布记录（= content × account：发到某平台账号 + 数据指标）'])
open_api.include_router(open_media_router, prefix='/medias', tags=['素材库；配图/封面/视频/模板（私有桶引用）-素材库；配图/封面/视频/模板（私有桶引用）'])
open_api.include_router(open_content_insight_router, prefix='/content/insights', tags=['内容洞察（复盘结构化结论，进化沉淀核心）-内容洞察（复盘结构化结论，进化沉淀核心）'])
open_api.include_router(open_viral_pattern_router, prefix='/viral/patterns', tags=['爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）-爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）'])
open_api.include_router(open_hot_topic_router, prefix='/hot/topics', tags=['热榜快照（全局，去重，喂选题；可选数据源）-热榜快照（全局，去重，喂选题；可选数据源）'])

# ========================================
# Agent API
# 路径前缀: /api/v1/hasn_creator/agent/
# ========================================
agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/creator/agent', tags=['运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度Agent'])

agent.include_router(agent_project_router, prefix='/project', tags=['运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度Agent-运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度'])
agent.include_router(agent_playbook_router, prefix='/playbooks', tags=['获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义-获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义'])
agent.include_router(agent_profile_router, prefix='/profiles', tags=['项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）-项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）'])
agent.include_router(agent_account_router, prefix='/accounts', tags=['平台账号（1:N project）；同一项目多平台真实账号-平台账号（1:N project）；同一项目多平台真实账号'])
agent.include_router(agent_competitor_router, prefix='/competitors', tags=['竞品账号（定位/选题调研输入）-竞品账号（定位/选题调研输入）'])
agent.include_router(agent_content_router, prefix='/contents', tags=['内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核-内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核'])
agent.include_router(agent_content_stage_router, prefix='/content/stages', tags=['阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播-阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播'])
agent.include_router(agent_topic_router, prefix='/topics', tags=['选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过-选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过'])
agent.include_router(agent_draft_router, prefix='/drafts', tags=['草稿箱（灵感快速捕获，轻量独立于正式流水线）-草稿箱（灵感快速捕获，轻量独立于正式流水线）'])
agent.include_router(agent_publish_router, prefix='/publishs', tags=['发布记录（= content × account：发到某平台账号 + 数据指标）-发布记录（= content × account：发到某平台账号 + 数据指标）'])
agent.include_router(agent_media_router, prefix='/medias', tags=['素材库；配图/封面/视频/模板（私有桶引用）-素材库；配图/封面/视频/模板（私有桶引用）'])
agent.include_router(agent_content_insight_router, prefix='/content/insights', tags=['内容洞察（复盘结构化结论，进化沉淀核心）-内容洞察（复盘结构化结论，进化沉淀核心）'])
agent.include_router(agent_viral_pattern_router, prefix='/viral/patterns', tags=['爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）-爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）'])
agent.include_router(agent_hot_topic_router, prefix='/hot/topics', tags=['热榜快照（全局，去重，喂选题；可选数据源）-热榜快照（全局，去重，喂选题；可选数据源）'])
