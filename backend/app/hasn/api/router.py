from fastapi import APIRouter

from backend.app.hasn.api.v1.admin.hasn_agent_capabilities import router as admin_hasn_agent_capabilities_router
from backend.app.hasn.api.v1.admin.hasn_agent_runtime_reports import router as admin_hasn_agent_runtime_reports_router
from backend.app.hasn.api.v1.admin.hasn_agents import router as admin_hasn_agents_router
from backend.app.hasn.api.v1.admin.hasn_app_beta_access import router as admin_hasn_app_beta_access_router
from backend.app.hasn.api.v1.admin.hasn_app_catalog import router as admin_hasn_app_catalog_router
from backend.app.hasn.api.v1.admin.hasn_app_entitlement import router as admin_hasn_app_entitlement_router
from backend.app.hasn.api.v1.admin.hasn_audit_log import router as admin_hasn_audit_log_router
from backend.app.hasn.api.v1.admin.hasn_channel_bindings import router as admin_hasn_channel_bindings_router
from backend.app.hasn.api.v1.admin.hasn_clients import router as admin_hasn_clients_router
from backend.app.hasn.api.v1.admin.hasn_contact_requests import router as admin_hasn_contact_requests_router
from backend.app.hasn.api.v1.admin.hasn_contacts import router as admin_hasn_contacts_router
from backend.app.hasn.api.v1.admin.hasn_conversations import router as admin_hasn_conversations_router
from backend.app.hasn.api.v1.admin.hasn_enterprise import router as admin_hasn_enterprise_router
from backend.app.hasn.api.v1.admin.hasn_enterprise_invite_code import router as admin_hasn_enterprise_invite_code_router
from backend.app.hasn.api.v1.admin.hasn_enterprise_membership import router as admin_hasn_enterprise_membership_router
from backend.app.hasn.api.v1.admin.hasn_group_members import router as admin_hasn_group_members_router
from backend.app.hasn.api.v1.admin.hasn_humans import router as admin_hasn_humans_router
from backend.app.hasn.api.v1.admin.hasn_messages import router as admin_hasn_messages_router
from backend.app.hasn.api.v1.admin.hasn_node_bindings import router as admin_hasn_node_bindings_router
from backend.app.hasn.api.v1.admin.hasn_nodes import router as admin_hasn_nodes_router
from backend.app.hasn.api.v1.admin.hasn_notifications import router as admin_hasn_notifications_router
from backend.app.hasn.api.v1.admin.hasn_owner_api_keys import router as admin_hasn_owner_api_keys_router
from backend.app.hasn.api.v1.admin.hasn_pending_intents import router as admin_hasn_pending_intents_router
from backend.app.hasn.api.v1.admin.hasn_platform_default_config import (
    router as admin_hasn_platform_default_config_router,
)
from backend.app.hasn.api.v1.admin.hasn_platform_operator_grants import (
    router as admin_hasn_platform_operator_grants_router,
)
from backend.app.hasn.api.v1.admin.hasn_session_artifacts import router as admin_hasn_session_artifacts_router
from backend.app.hasn.api.v1.admin.hasn_session_events import router as admin_hasn_session_events_router
from backend.app.hasn.api.v1.admin.hasn_sessions import router as admin_hasn_sessions_router
from backend.app.hasn.api.v1.admin.hasn_suppressed_messages import router as admin_hasn_suppressed_messages_router
from backend.app.hasn.api.v1.admin.hasn_sync_events import router as admin_hasn_sync_events_router
from backend.app.hasn.api.v1.admin.hasn_sync_inbox_events import router as admin_hasn_sync_inbox_events_router
from backend.app.hasn.api.v1.admin.hasn_task import router as admin_hasn_task_router
from backend.app.hasn.api.v1.admin.hasn_task_run import router as admin_hasn_task_run_router
from backend.app.hasn.api.v1.admin.hasn_trade_sessions import router as admin_hasn_trade_sessions_router
from backend.app.hasn.api.v1.admin.hasn_unread_counts import router as admin_hasn_unread_counts_router
from backend.app.hasn.api.v1.ai_native_app import apps_router as ai_native_apps_router
from backend.app.hasn.api.v1.ai_native_app import audit_router as ai_native_audit_router
from backend.app.hasn.api.v1.ai_native_app import runtime_router as ai_native_runtime_router
from backend.app.hasn.api.v1.message_hub import router as message_hub_router

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn.api.v1.onboarding import router as onboarding_router
from backend.app.hasn.api.v1.sync import router as sync_router
from backend.app.hasn_task.api.v1.admin.skill_bundle import router as admin_hasn_skill_bundle_router
from backend.core.conf import settings

ai_native = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/ai-native', tags=['AI-Native 应用平台'])
ai_native.include_router(ai_native_apps_router, prefix='/apps', tags=['AI-Native 应用平台-应用'])
ai_native.include_router(ai_native_runtime_router, prefix='/runtime', tags=['AI-Native 应用平台-运行时'])
ai_native.include_router(ai_native_audit_router, prefix='/audit', tags=['AI-Native 应用平台-审计'])

v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn', tags=['HASN 管理端'])

v1.include_router(onboarding_router, tags=['HASN Onboarding'])
v1.include_router(message_hub_router, tags=['HASN MessageHub'])
v1.include_router(sync_router, tags=['HASN Sync'])
v1.include_router(admin_hasn_humans_router, prefix='/humans', tags=['用户管理'])
v1.include_router(admin_hasn_agents_router, prefix='/agents', tags=['Agent管理'])
v1.include_router(admin_hasn_contacts_router, prefix='/contacts', tags=['联系人管理'])
v1.include_router(admin_hasn_contact_requests_router, prefix='/contact-requests', tags=['好友请求管理'])
v1.include_router(admin_hasn_conversations_router, prefix='/conversations', tags=['会话管理'])
v1.include_router(admin_hasn_messages_router, prefix='/messages', tags=['消息管理'])
v1.include_router(admin_hasn_unread_counts_router, prefix='/unread/counts', tags=['未读计数'])
v1.include_router(admin_hasn_group_members_router, prefix='/group/members', tags=['群成员管理'])
v1.include_router(admin_hasn_agent_capabilities_router, prefix='/agent/capabilities', tags=['Agent能力'])
v1.include_router(admin_hasn_trade_sessions_router, prefix='/trade/sessions', tags=['交易会话'])
v1.include_router(admin_hasn_notifications_router, prefix='/notifications', tags=['通知管理'])
v1.include_router(admin_hasn_audit_log_router, prefix='/audit/logs', tags=['审计日志'])
v1.include_router(admin_hasn_nodes_router, prefix='/hasn/nodess', tags=['HASN Node 主-HASN Node 主'])
v1.include_router(
    admin_hasn_owner_api_keys_router, prefix='/hasn/owner/api/keyss', tags=['HASN Owner API Key -HASN Owner API Key ']
)
v1.include_router(
    admin_hasn_node_bindings_router,
    prefix='/hasn/node/bindingss',
    tags=['HASN Node Owner Binding 租约-HASN Node Owner Binding 租约'],
)
v1.include_router(admin_hasn_agent_runtime_reports_router, prefix='/runtime/reports', tags=['HASN Runtime reports'])
v1.include_router(admin_hasn_channel_bindings_router, prefix='/channel/bindings', tags=['HASN Channel bindings'])
v1.include_router(admin_hasn_clients_router, prefix='/clients', tags=['HASN Clients'])
v1.include_router(admin_hasn_pending_intents_router, prefix='/pending/intents', tags=['HASN Pending intents'])
v1.include_router(
    admin_hasn_suppressed_messages_router, prefix='/suppressed/messages', tags=['HASN Suppressed messages']
)
v1.include_router(admin_hasn_sync_events_router, prefix='/sync/events', tags=['HASN Sync events'])
v1.include_router(admin_hasn_sync_inbox_events_router, prefix='/sync/inbox/events', tags=['HASN Sync inbox events'])
v1.include_router(admin_hasn_enterprise_router, prefix='/enterprises', tags=['企业管理'])
v1.include_router(admin_hasn_enterprise_membership_router, prefix='/enterprise/memberships', tags=['企业成员关系'])
v1.include_router(admin_hasn_enterprise_invite_code_router, prefix='/enterprise/invite-codes', tags=['企业邀请码'])
# 应用平台 v3 P3（设计 17 决策①②）：active-workspaces / workspace-apps 管理路由随两表退役一并删除。

# --- 用户端（仅 JWT） ---
from backend.app.hasn.api.agent_scopes import router as agent_scopes_router
from backend.app.hasn.api.v1.app.hasn_agent_capabilities import router as app_hasn_agent_capabilities_router
from backend.app.hasn.api.v1.app.hasn_agent_channel_mirrors import router as app_hasn_agent_channel_mirrors_router
from backend.app.hasn.api.v1.app.hasn_agents import router as app_hasn_agents_router
from backend.app.hasn.api.v1.app.hasn_audit_log import router as app_hasn_audit_log_router
from backend.app.hasn.api.v1.app.hasn_conversations import router as app_hasn_conversations_router
from backend.app.hasn.api.v1.app.hasn_group_members import router as app_hasn_group_members_router
from backend.app.hasn.api.v1.app.hasn_groups import router as app_hasn_groups_router
from backend.app.hasn.api.v1.app.hasn_humans import router as app_hasn_humans_router
from backend.app.hasn.api.v1.app.hasn_messages import router as app_hasn_messages_router
from backend.app.hasn.api.v1.app.hasn_notifications import router as app_hasn_notifications_router
from backend.app.hasn.api.v1.app.hasn_trade_sessions import router as app_hasn_trade_sessions_router
from backend.app.hasn.api.v1.app.hasn_unread_counts import router as app_hasn_unread_counts_router
from backend.app.hasn.api.v1.app.judge import router as app_judge_router
from backend.app.hasn.api.v1.app.knowledge import router as app_knowledge_router
from backend.app.hasn.api.v1.app.owner_memory import router as app_owner_memory_router
from backend.app.hasn.api.v1.app.platform_config import router as app_platform_config_router
from backend.app.hasn.api.v1.app.speech_catalog import router as app_speech_catalog_router
from backend.app.hasn.api.v1.app.suppressed_release import router as app_suppressed_release_router
from backend.app.hasn_memory.api.router import app_owner_profile_coverage_router

app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn/app', tags=['HASN 用户端'])

app.include_router(app_hasn_humans_router, prefix='/humans', tags=['用户管理'])
app.include_router(app_hasn_agents_router, prefix='/agents', tags=['Agent管理'])
app.include_router(
    app_hasn_agent_channel_mirrors_router,
    prefix='/agent-channel-mirrors',
    tags=['Agent 渠道脱敏摘要跨设备镜像（仅可见性）'],
)
app.include_router(app_hasn_conversations_router, prefix='/conversations', tags=['会话管理'])
app.include_router(app_hasn_messages_router, prefix='/messages', tags=['消息管理'])
app.include_router(app_hasn_unread_counts_router, prefix='/unread/counts', tags=['未读计数'])
app.include_router(app_hasn_group_members_router, prefix='/group/members', tags=['群成员管理'])
app.include_router(app_hasn_groups_router, prefix='/groups', tags=['群组（建群/群管理）'])
app.include_router(app_hasn_agent_capabilities_router, prefix='/agent/capabilities', tags=['Agent能力'])
app.include_router(app_hasn_trade_sessions_router, prefix='/trade/sessions', tags=['交易会话'])
app.include_router(app_hasn_notifications_router, prefix='/notifications', tags=['通知管理'])
app.include_router(app_hasn_audit_log_router, prefix='/audit/logs', tags=['审计日志'])
app.include_router(app_knowledge_router, tags=['知识库'])
app.include_router(app_owner_memory_router, prefix='/owner', tags=['Owner 记忆（主人透明）'])
app.include_router(app_owner_profile_coverage_router, prefix='/owner', tags=['主人画像完整度（了解主人）'])
app.include_router(app_platform_config_router, prefix='/platform', tags=['平台默认配置（节点下发）'])
app.include_router(app_speech_catalog_router, prefix='/speech-catalog', tags=['通用语音模型签名目录（节点下发）'])
app.include_router(app_suppressed_release_router, tags=['入站门控抑制箱放行'])
app.include_router(app_judge_router, tags=['通用LLM裁判（出站披露/A2A终止）'])
app.include_router(agent_scopes_router, tags=['Agent权限管理'])

# --- Agent（Agent Key） ---
from backend.app.hasn.api.v1.agent.hasn_agent_capabilities import router as agent_hasn_agent_capabilities_router
from backend.app.hasn.api.v1.agent.hasn_agent_profile import router as agent_hasn_agent_profile_router
from backend.app.hasn.api.v1.agent.hasn_agent_runtime import router as agent_hasn_agent_runtime_router
from backend.app.hasn.api.v1.agent.hasn_agents import router as agent_hasn_agents_router
from backend.app.hasn.api.v1.agent.hasn_audit_log import router as agent_hasn_audit_log_router
from backend.app.hasn.api.v1.agent.hasn_contacts import router as agent_hasn_contacts_router
from backend.app.hasn.api.v1.agent.hasn_conversations import router as agent_hasn_conversations_router
from backend.app.hasn.api.v1.agent.hasn_group_members import router as agent_hasn_group_members_router
from backend.app.hasn.api.v1.agent.hasn_groups import router as agent_hasn_groups_router
from backend.app.hasn.api.v1.agent.hasn_humans import router as agent_hasn_humans_router
from backend.app.hasn.api.v1.agent.hasn_messages import router as agent_hasn_messages_router
from backend.app.hasn.api.v1.agent.hasn_nodes import router as agent_hasn_nodes_router
from backend.app.hasn.api.v1.agent.hasn_notifications import router as agent_hasn_notifications_router
from backend.app.hasn.api.v1.agent.hasn_session_artifacts import router as agent_hasn_session_artifacts_router
from backend.app.hasn.api.v1.agent.hasn_session_events import router as agent_hasn_session_events_router
from backend.app.hasn.api.v1.agent.hasn_sessions import router as agent_hasn_sessions_router
from backend.app.hasn.api.v1.agent.hasn_task_run import router as agent_hasn_task_run_router
from backend.app.hasn.api.v1.agent.hasn_trade_sessions import router as agent_hasn_trade_sessions_router
from backend.app.hasn.api.v1.agent.hasn_unread_counts import router as agent_hasn_unread_counts_router
from backend.app.hasn_task.api.v1.agent.skill_bundle import router as agent_hasn_skill_bundle_router

agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn/agent', tags=['HASN Agent端'])

agent.include_router(agent_hasn_humans_router, prefix='/humans', tags=['用户管理'])
agent.include_router(agent_hasn_agents_router, prefix='/agents', tags=['Agent管理'])
agent.include_router(agent_hasn_agent_profile_router, tags=['Agent Profile（云端权威）'])
agent.include_router(agent_hasn_agent_runtime_router, prefix='/runtime', tags=['云端 Runtime 派发代理（双形态）'])
agent.include_router(agent_hasn_contacts_router, prefix='/contacts', tags=['联系人管理'])
agent.include_router(agent_hasn_conversations_router, prefix='/conversations', tags=['会话管理'])
agent.include_router(agent_hasn_messages_router, prefix='/messages', tags=['消息管理'])
agent.include_router(agent_hasn_unread_counts_router, prefix='/unread/counts', tags=['未读计数'])
agent.include_router(agent_hasn_group_members_router, prefix='/group/members', tags=['群成员管理'])
agent.include_router(agent_hasn_groups_router, prefix='/groups', tags=['群组（分身只读）'])
agent.include_router(agent_hasn_agent_capabilities_router, prefix='/agent/capabilities', tags=['Agent能力'])
agent.include_router(agent_hasn_trade_sessions_router, prefix='/trade/sessions', tags=['交易会话'])
# 保留：legacy 任务调度协议 task-result 上报 + run 读取（Agent JWT）；任务 CRUD 已收口 hasn_task 应用
agent.include_router(agent_hasn_task_run_router, prefix='/hasn/task/runs', tags=['任务执行记录-任务执行记录'])
agent.include_router(agent_hasn_notifications_router, prefix='/notifications', tags=['通知管理'])
agent.include_router(agent_hasn_audit_log_router, prefix='/audit/logs', tags=['审计日志'])
agent.include_router(agent_hasn_nodes_router, prefix='/hasn/nodess', tags=['HASN Node 主-HASN Node 主'])
agent.include_router(
    agent_hasn_skill_bundle_router,
    prefix='/hasn/skill/bundles',
    tags=['Skill Bundle 定义表（多个 skill 的组合）-Skill Bundle 定义表（多个 skill 的组合）'],
)
agent.include_router(
    agent_hasn_sessions_router, prefix='/hasn/sessionss', tags=['HASN 会话分层 - 逻辑会话-HASN 会话分层 - 逻辑会话']
)
agent.include_router(
    agent_hasn_session_events_router, prefix='/hasn/session/eventss', tags=['HASN 会话事件-HASN 会话事件']
)
agent.include_router(
    agent_hasn_session_artifacts_router, prefix='/hasn/session/artifactss', tags=['HASN 会话产物-HASN 会话产物']
)

# --- 公开（无需认证，仅 Agent 能力发现） ---
from backend.app.hasn.api.v1.open.hasn_agent_capabilities import router as open_hasn_agent_capabilities_router
from backend.app.hasn.api.v1.open.hasn_session_artifacts import router as open_hasn_session_artifacts_router
from backend.app.hasn.api.v1.open.hasn_session_events import router as open_hasn_session_events_router
from backend.app.hasn.api.v1.open.hasn_sessions import router as open_hasn_sessions_router

open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn/open', tags=['HASN 公开接口'])

open_api.include_router(open_hasn_agent_capabilities_router, prefix='/agent/capabilities', tags=['Agent能力发现'])
# 注：任务为 owner 私有资源；旧 codegen 的 agent/open 任务端点无 owner 隔离（泄漏全量任务），
# 已随 hasn_task 应用化（设计 06 §1.1/§10）摘除。Agent 任务能力面 = /api/v1/hasn-task/agent（Agent JWT，M2）。
# 注：hasn_skill_bundle 是 owner 私有任务域资源，无 status/visibility 列，不该有公开端点。
# open scope 已删除（曾把所有 owner 私有 bundle 无鉴权暴露给匿名）。浏览走 app/agent scope。
open_api.include_router(
    open_hasn_sessions_router, prefix='/hasn/sessionss', tags=['HASN 会话分层 - 逻辑会话-HASN 会话分层 - 逻辑会话']
)
open_api.include_router(
    open_hasn_session_events_router, prefix='/hasn/session/eventss', tags=['HASN 会话事件-HASN 会话事件']
)
open_api.include_router(
    open_hasn_session_artifacts_router, prefix='/hasn/session/artifactss', tags=['HASN 会话产物-HASN 会话产物']
)
# open_hasn_nodes_router 已移除（v2.1: 节点注册在 WS 建连时自动完成）

# --- WebSocket 端点（统一节点） ---
from backend.app.hasn_im.api.ws_node import router as ws_node_router

ws = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn', tags=['HASN WebSocket'])

ws.include_router(ws_node_router)

# --- 用户端业务 API（联系人 + 认证 + 搜索） ---
from backend.app.hasn.api.v1.app.contacts import router as app_contacts_router
from backend.app.hasn.api.v1.app.enterprise import router as enterprise_router
from backend.app.hasn.api.v1.app.hasn_api_keys import router as app_hasn_api_keys_router
from backend.app.hasn.api.v1.app.hasn_auth_api import router as app_hasn_auth_router
from backend.app.hasn.api.v1.app.hasn_nodes import router as app_hasn_nodes_router
from backend.app.hasn.api.v1.app.hasn_owner_api_keys import router as app_hasn_owner_api_keys_router
from backend.app.hasn.api.v1.app.profile import router as app_profile_router
from backend.app.hasn.api.v1.app.search import router as app_users_search_router
from backend.app.hasn.api.v1.app.workspace import router as workspace_router
from backend.app.hasn.api.v1.node_control import router as node_control_router

app.include_router(app_contacts_router, tags=['联系人管理'])
app.include_router(app_hasn_auth_router, tags=['HASN认证'])
v1.include_router(enterprise_router, tags=['企业与工作空间'])
v1.include_router(workspace_router, tags=['工作区切换'])
app.include_router(app_users_search_router, tags=['HASN Users'])
app.include_router(app_profile_router, prefix='/profile', tags=['合并 Profile (sys_user + hasn_humans)'])

# --- IM 业务 API ---
from backend.app.hasn.api.v1.app.hasn_agent_mcp_keys import router as app_hasn_agent_mcp_keys_router
from backend.app.hasn.api.v1.app.hasn_assets_app import router as app_hasn_assets_router
from backend.app.hasn.api.v1.app.hasn_im import router as app_hasn_im_router
from backend.app.hasn.api.v1.app.hasn_session_artifacts import router as app_hasn_session_artifacts_router
from backend.app.hasn.api.v1.app.hasn_session_events import router as app_hasn_session_events_router
from backend.app.hasn.api.v1.app.hasn_sessions import router as app_hasn_sessions_router
from backend.app.hasn.api.v1.app.hasn_task_sessions import router as app_hasn_task_sessions_router
from backend.app.hasn.api.v1.app.hasn_task_sessions import work_sessions_router
from backend.app.hasn_task.api.v1.app.skill_bundle import router as app_hasn_skill_bundle_router

app.include_router(app_hasn_im_router, prefix='/im', tags=['HASN IM 业务'])
app.include_router(app_hasn_api_keys_router, tags=['HASN API Key'])
app.include_router(app_hasn_nodes_router, prefix='/hasn/nodess', tags=['HASN Node 主-HASN Node 主'])
app.include_router(
    app_hasn_skill_bundle_router,
    prefix='/hasn/skill/bundles',
    tags=['Skill Bundle 定义表（多个 skill 的组合）-Skill Bundle 定义表（多个 skill 的组合）'],
)
app.include_router(
    app_hasn_owner_api_keys_router, prefix='/hasn/owner/api/keyss', tags=['HASN Owner API Key -HASN Owner API Key ']
)
app.include_router(
    app_hasn_sessions_router, prefix='/hasn/sessionss', tags=['HASN 会话分层 - 逻辑会话-HASN 会话分层 - 逻辑会话']
)
app.include_router(app_hasn_session_events_router, prefix='/hasn/session/eventss', tags=['HASN 会话事件-HASN 会话事件'])
app.include_router(
    app_hasn_session_artifacts_router, prefix='/hasn/session/artifactss', tags=['HASN 会话产物-HASN 会话产物']
)
app.include_router(app_hasn_task_sessions_router, tags=['任务系统 Session API'])
app.include_router(app_hasn_agent_mcp_keys_router, prefix='/agent-mcp-keys', tags=['Agent MCP 接入凭证'])
app.include_router(app_hasn_assets_router, prefix='/assets', tags=['HASN 资产（消息附件上传/解析）'])
v1.include_router(work_sessions_router, tags=['外部 APP 工作会话'])
v1.include_router(node_control_router, tags=['HASN Node 控制平面'])
v1.include_router(
    admin_hasn_skill_bundle_router,
    prefix='/hasn/skill/bundles',
    tags=['Skill Bundle 定义表（多个 skill 的组合）-Skill Bundle 定义表（多个 skill 的组合）'],
)
v1.include_router(admin_hasn_task_router, prefix='/hasn/tasks', tags=['任务定义-任务定义'])
v1.include_router(admin_hasn_task_run_router, prefix='/hasn/task/runs', tags=['任务执行记录-任务执行记录'])
v1.include_router(
    admin_hasn_sessions_router, prefix='/hasn/sessionss', tags=['HASN 会话分层 - 逻辑会话-HASN 会话分层 - 逻辑会话']
)
v1.include_router(
    admin_hasn_session_events_router, prefix='/hasn/session/eventss', tags=['HASN 会话事件-HASN 会话事件']
)
v1.include_router(
    admin_hasn_session_artifacts_router, prefix='/hasn/session/artifactss', tags=['HASN 会话产物-HASN 会话产物']
)
v1.include_router(admin_hasn_app_catalog_router, prefix='/app-catalogs', tags=['AI-Native 应用目录'])
v1.include_router(admin_hasn_app_entitlement_router, prefix='/app-entitlements', tags=['AI-Native 应用权益'])
v1.include_router(admin_hasn_app_beta_access_router, prefix='/app-beta-access', tags=['AI-Native 应用灰度内测'])
v1.include_router(
    admin_hasn_platform_default_config_router, prefix='/platform-default-config', tags=['平台默认配置（节点下发）']
)
v1.include_router(
    admin_hasn_platform_operator_grants_router, prefix='/platform-operator-grants', tags=['平台运维授予源（G1 特权门）']
)

# --- 分身产物（Artifacts，AF-2）：独立顶层路由组 /api/v1/artifacts/*（不挂 /hasn 下，平台 primitive）---
from backend.app.hasn.api.v1.agent.hasn_artifacts import router as agent_hasn_artifacts_router
from backend.app.hasn.api.v1.app.hasn_artifacts import router as app_hasn_artifacts_router

artifacts_agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/artifacts/agent', tags=['分身产物 Agent端'])
artifacts_agent.include_router(agent_hasn_artifacts_router)

artifacts_app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/artifacts/app', tags=['分身产物 用户端'])
artifacts_app.include_router(app_hasn_artifacts_router)

# --- CI 发布面（Bearer 发布密钥，非 JWT）：离线发布方一键发布语音模型签名目录 ---
from backend.app.hasn.api.v1.ci.speech_catalog import router as ci_speech_catalog_router

ci = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn/ci', tags=['HASN CI 发布面'])
ci.include_router(ci_speech_catalog_router, prefix='/speech-catalog', tags=['通用语音模型签名目录发布（CI Bearer）'])
