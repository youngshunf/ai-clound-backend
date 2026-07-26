"""配置/目录变更的 WS 主动推送与 revision 缓存（设计 doc02-数据与同步/07）。

职责：
  - 为 3 类全局配置/目录维护内容指纹 revision，缓存进 Redis（``hasn:sync:rev:{kind}``）。
  - bump：写点变更后重算 revision + 写缓存 + 经 ``ws_router`` 向在线节点 push
    ``hasn.sync.invalidate``（在线 daemon 秒级收到去拉最新，不再绑死登录/轮询）。
  - get_all_revisions：连接握手用，读缓存（miss 即重算并回填），cheap。

单 worker 部署（``deploy --workers 1`` / ``granian --workers 1``）：``ws_router._ws_connections``
是进程内全部连接，fan-out 直接遍历即完整覆盖，无需 Redis pub/sub 广播。离线节点不入离线队列——
invalidate 是幂等「去拉最新」信号，靠重连 ``hasn.connected`` 握手对账追平。

revision 范式对齐 ``common_skills_service`` / ``platform_default_config_service``：内容变 → 指纹变。
"""

from __future__ import annotations

import hashlib
import logging

from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa

from backend.database.redis import redis_client

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


_node_session_gateway: Any | None = None


def _get_node_session_gateway():
    global _node_session_gateway
    if _node_session_gateway is None:
        from backend.app.hasn_im.application.provider import get_node_session_gateway

        _node_session_gateway = cast(Any, get_node_session_gateway())
    return _node_session_gateway

# revision 缓存键前缀
REV_PREFIX = 'hasn:sync:rev'
# 缓存 TTL（兜底：写点漏 bump 时，最长这么久后 get_all_revisions 缓存过期重算自愈）
REV_TTL_SECS = 3600

KIND_BUILTIN_CATALOG = 'builtin_catalog'
KIND_COMMON_SKILLS = 'common_skills'
KIND_PLATFORM_CONFIG = 'platform_config'
KIND_DESIGNSYSTEM = 'designsystem'
# 通用语音模型签名目录（SPCAT-4）：全局单行 catalog，发布即 bump，daemon 比对重拉验签写盘。
KIND_SPEECH_CATALOG = 'speech_catalog'
# 全局 kind：单一全局 revision，进 get_all_revisions 握手快照，bump(owner_id=None) 全局广播。
KINDS = (
    KIND_BUILTIN_CATALOG,
    KIND_COMMON_SKILLS,
    KIND_PLATFORM_CONFIG,
    KIND_DESIGNSYSTEM,
    KIND_SPEECH_CATALOG,
)

# owner 定向 kind（doc02-07 LF-P3）：revision 是「某 owner 维度」的指纹，对全局握手无意义，
# 故**不进 KINDS / get_all_revisions 握手快照**——离线追平靠该 owner 任务镜像的周期 sync_pull，
# 在线即时刷新靠 bump_owner 向该 owner 在线节点 push。daemon 收到不据 revision 去重、收到即拉。
KIND_TASKS = 'tasks'
# owner 定向：规划应用（plan）数据变更（任一设备/分身增删改目标/计划/待办/日程/习惯）。
# 与 tasks 同形态——per-owner 指纹，不进全局握手，靠 bump_owner push + 周期 sync_pull 兜底。
KIND_PLAN = 'plan'
# owner 定向：入站门控抑制箱（外部→Agent 被门控的消息记录变更）。daemon 收到即拉 owner 的
# 抑制箱镜像（含云端门控产出的 social_disabled/permission_denied/agent_frozen/abuse_restricted/manual_only）。
KIND_SUPPRESSED = 'suppressed'
# owner 定向：该 owner 名下 Agent 画像下行（owner_memory 合并后覆盖各 Agent ``user_md`` + bump
# ``profile_revision``；或单 Agent 资料编辑）。daemon 收到即全量重拉 agents 镜像（刷新本地
# agent_memory 的 USER.md/SOUL.md），并主动把新 USER.md 写进在线 hermes 工作区——不等下次派发
# 即生效（KNOWU 采访完画像秒级下发到运行时）。
KIND_AGENTS = 'agents'
# owner 定向：该 owner 的记忆命名空间下行（doc17 peer 画像合成后即时失效）——云端合成/写入
# 记忆权威后，主动 push 该 owner 在线节点「memory 维度变了」→ daemon 收到即触发 memory
# sync_pull（拉 owner 各记忆命名空间：portraits/facts/…）落本地镜像。离线设备靠登录/重连
# 的 pull_once 兜底追平。
KIND_MEMORY = 'memory'
# owner 定向：该 owner 名下社区内容变更（LFRT 刀4，实施/90 §2.1）——分身经云端面
# （平台 MCP 工具 / Agent REST 共用的 community_tool_handlers）发帖/发文/评论/互动后 bump，
# daemon 收到即 nudge webui 重拉社区视图（local_first 热路径回镜像 + 机会刷新拉云端，
# 镜像变化再推第二次失效收敛）。治「分身发完帖，主人这边 feed/我的帖子迟迟不出现」。
KIND_COMMUNITY = 'community'
# owner 定向：该 owner 的演示文稿变更（LFRT 刀4，同上）——分身经云端 deck 平台工具
# 建 deck/写页/改页后 bump，daemon 收到即 nudge webui 重拉 deck 列表/详情（读走
# read-through 合并引擎，refetch 即拉到云端最新）。治「PPT 做完打开空白/不出现」。
KIND_DECKS = 'decks'
# owner 定向：该 owner 名下任一 Agent 的三态授权（hasn_agent_scopes.{default_mode,capability_modes}）
# 变更（实施102 S4·U-L4）——主人权限页改三态 / 审批「总是允许」写穿后 bump，daemon 收到即刷新
# 该 owner 名下 Agent 的 CapabilityModeMirror（本地镜像与云端权威对齐），治「设备 A 改了三态、
# 设备 B 镜像不更新」。离线设备靠重连握手 sync_pull 兜底。
KIND_SCOPES = 'scopes'
# owner 定向：该 owner 的统一通知中心「通知面」有新通知（NOTIFUX-3）——外部用户/agent 触发的
# 点赞/关注/分享等经 notification_service.emit() 落权威行后 bump，daemon 收到即拉 owner 未读通知、
# diff 出新增未读项发原生系统通知（点击深链到通知覆盖层），并 nudge webui 刷新通知列表+未读徽标。
# 与 suppressed 同形态（per-owner 指纹，不进全局握手，靠 bump_owner push + 周期 sync_pull 兜底）；
# 「自分身→主人」的汇报面走会话汇报卡（emit 内 OwnerLoopback 早返），绝不进此 kind。
KIND_NOTIFICATION = 'notification'
# owner 定向：该 owner 参与的群设置/成员/生效发言策略变更（doc10 群聊发言规则）——加/减分身触发
# effective_agent_policy 翻转、改「允许成员拉分身」开关、拉分身邀请 accept/decline 等，bump 该群
# 全体成员 owner。daemon 收到即 nudge webui 重拉群详情（读走 local_first），刷新常驻「生效发言规则」
# 徽标。charter 变更**不进**此 kind（隐私：准则仅主人可见，不向全群广播；走 session 水位重建，见 daemon D4）。
KIND_GROUPS = 'groups'
# owner 定向：该 owner 的商业化状态变更（统一商业化内核·实施/92 MK-5）——订阅升降级
# （UserSubscription.tier/status/subscription_end_date 变）、权益授予/到期/撤销
# （HasnAppEntitlement.status/expires_at 变）、统一 sweeper 的提醒/宽限/终态迁移后 bump。
# daemon 收到即拉该 owner 的费用账单中心镜像（订阅档 + 权益总账 + 到期提醒），刷新 webui
# BillingCenterPage 与付费墙判定缓存。与 notification 同形态（per-owner 指纹，不进全局握手，
# 靠 bump_owner push + 周期 sync_pull 兜底）。「到期提醒」的原生系统通知走 notification kind，
# 此 kind 只负责账单中心数据镜像失效。
KIND_BILLING = 'billing'
# owner 定向：联系人在线态实时刷新。某分身在线态翻转 online↔offline 时，向「能在通讯录里看到
# 该分身」的 owner 们 bump（直接加该分身为好友的 owner + 加了该分身主人为好友、在 owned_agents
# 里看到它的 owner）。daemon 收到即强制回源刷新本地联系人镜像（云端 `_build_contact_detail`
# 会重算 peer/owned_agents 的实时 presence）+ nudge webui 重拉联系人列表，让好友分身的在线圆点
# 实时翻绿/变灰——此前只在联系人列表下次重刷（窗口聚焦/导航）时才更新，跨主人/跨设备看好友分身
# 在线态长时间不刷新。revision 仅作 invalidate 帧字段（presence 在 Redis、不进表指纹，daemon 也
# 不据 revision 去重、收到即拉）——push 本身即触发刷新。
KIND_CONTACTS = 'contacts'
# owner 定向：该 owner 名下平台项目（doc38 U5，app_id=project）数据变更——任一设备/分身经用户端
# API（/api/v1/project/app/*）或 hasn.project.* 平台工具建/改/归档项目、增删改里程碑、挂靠/摘除资源后
# bump。daemon 收到即拉该 owner 的项目镜像（local_first）。与 plan/tasks 同形态——per-owner 指纹，
# 不进全局握手，靠 bump_owner push + 周期 sync_pull 兜底。
KIND_PROJECT = 'project'
# owner 定向：该 owner 的金融投研六类产物或自选股发生有效 create/update/delete。
# revision 聚合 hasn_finance 七表并保留 tombstone，daemon 收到即后台 read-through 合并本地镜像，
# 再通知 WebUI 只刷新当前命中的 finance query。离线设备靠下一次机会刷新追平。
KIND_FINANCE = 'finance'
OWNER_KINDS = (
    KIND_TASKS,
    KIND_PLAN,
    KIND_SUPPRESSED,
    KIND_AGENTS,
    KIND_MEMORY,
    KIND_COMMUNITY,
    KIND_DECKS,
    KIND_SCOPES,
    KIND_NOTIFICATION,
    KIND_GROUPS,
    KIND_BILLING,
    KIND_CONTACTS,
    KIND_PROJECT,
    KIND_FINANCE,
)
# 某 owner 任务镜像为空时的稳定指纹
EMPTY_TASKS_REVISION = 'empty'
# 某 owner 规划数据为空时的稳定指纹（同上约定）
EMPTY_PLAN_REVISION = 'empty'
# 某 owner 抑制箱为空时的稳定指纹（同上约定）
EMPTY_SUPPRESSED_REVISION = 'empty'
# 某 owner 名下无 Agent 时的稳定指纹（同上约定）
EMPTY_AGENTS_REVISION = 'empty'
# 某 owner 名下无社区内容时的稳定指纹（同上约定）
EMPTY_COMMUNITY_REVISION = 'empty'
# 某 owner 名下无 deck 时的稳定指纹（同上约定）
EMPTY_DECKS_REVISION = 'empty'
# 某 owner 名下无 Agent 三态授权行时的稳定指纹（同上约定）
EMPTY_SCOPES_REVISION = 'empty'
# 某 owner 名下无通知时的稳定指纹（同上约定）
EMPTY_NOTIFICATION_REVISION = 'empty'
# 某 owner 未参与任何群时的稳定指纹（同上约定）
EMPTY_GROUPS_REVISION = 'empty'
# 某 owner 无订阅/权益时的稳定指纹（同上约定）
EMPTY_BILLING_REVISION = 'empty'
# 某 owner 名下无平台项目时的稳定指纹（同上约定）
EMPTY_PROJECT_REVISION = 'empty'
# 某 owner 没有任何金融资源时的稳定指纹。
EMPTY_FINANCE_REVISION = 'empty'
# 联系人在线态失效的固定 revision。presence 在 Redis、不进表指纹，且 daemon 收到即拉不据
# revision 去重、owner 定向 kind 又是零 jitter——故 revision 值无实际作用，用固定串即可，
# 避免每次翻转都对 hasn_contacts 做一次纯为算指纹的查询。
CONTACTS_PRESENCE_REVISION = 'presence'

# 内置任务目录为空时的稳定指纹（对齐 common_skills 的 EMPTY 约定）
EMPTY_BUILTIN_CATALOG_REVISION = 'empty'
# 设计系统库为空时的稳定指纹（同上约定）
EMPTY_DESIGNSYSTEM_REVISION = 'empty'


async def compute_designsystem_revision(db: AsyncSession) -> str:
    """设计系统全局指纹：sha256(sorted "id@content_hash@project_id" 行)[:16]。

    任一设计系统的内容、项目挂靠或成员增删发生变化 → 指纹变 → 在线节点对账各自 owner 镜像
    （云端权威，节点只拉自己可见域 builtin∪owner∪共享）。软删行（``deleted_time`` 非空）
    落出集合 → 集合缩小 → 指纹变 → 镜像感知下线。
    """
    from backend.app.hasn_designsystem.model.design_system import DesignSystem

    rows = (
        await db.execute(
            sa.select(
                DesignSystem.id,
                DesignSystem.content_hash,
                DesignSystem.platform_project_id,
            ).where(DesignSystem.deleted_time.is_(None))
        )
    ).all()
    lines = sorted(
        f'{ds_id}@{content_hash or ""}@{platform_project_id or ""}'
        for ds_id, content_hash, platform_project_id in rows
    )
    if not lines:
        return EMPTY_DESIGNSYSTEM_REVISION
    signature = '\n'.join(lines)
    return hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]


async def compute_builtin_catalog_revision(db: AsyncSession) -> str:
    """内置任务目录指纹：sha256(sorted "key@revision" 行)[:16]，对齐 common_skills_revision。

    catalog 任一行的 ``revision``（per-row 版本号）或成员增减 → 指纹变 → daemon 重拉。
    """
    from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog

    rows = (await db.execute(sa.select(HasnBuiltinTaskCatalog.builtin_key, HasnBuiltinTaskCatalog.revision))).all()
    lines = sorted(f'{key}@{rev}' for key, rev in rows if key)
    if not lines:
        return EMPTY_BUILTIN_CATALOG_REVISION
    signature = '\n'.join(lines)
    return hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]


async def compute_owner_tasks_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 的任务镜像指纹：sha256(sorted "task_uuid@task_revision" 行)[:16]。

    ``task_revision`` 是任务定义的服务端单调修订号，任一任务被建/改（含状态机迁移：审批、
    暂停、删除软标记等都会 bump revision 或增删行）→ 集合内某行的指纹变 → 整体指纹变。
    仅作 invalidate 帧的 ``revision`` 字段：daemon 不据此去重、收到即拉，值变即「该 owner
    有任务变化」的信号。软删行（``deleted_at`` 非空）落出集合 → 删一个 → 集合缩小 → 指纹变。
    """
    from backend.app.hasn_task.model.task import HasnTask

    rows = (
        await db.execute(
            sa.select(HasnTask.task_uuid, HasnTask.task_revision).where(
                HasnTask.owner_id == owner_id, HasnTask.deleted_at.is_(None)
            )
        )
    ).all()
    lines = sorted(f'{task_uuid}@{revision}' for task_uuid, revision in rows if task_uuid)
    if not lines:
        return EMPTY_TASKS_REVISION
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def compute_owner_plan_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 的规划数据指纹：sha256(sorted "table:id@updated_time" 行)[:16]。

    跨核心五表（goal/plan/todo/event/habit）聚合该 owner 的行，任一行被建/改/软移除
    （status→archived/cancelled/done 也会落 ``updated_time``）→ 集合内某行指纹变 → 整体指纹变。
    仅作 invalidate 帧的 ``revision`` 字段：daemon 不据此去重、收到即拉该 owner 的 plan 镜像。
    """
    from backend.app.hasn_plan.model.event import Event
    from backend.app.hasn_plan.model.goal import Goal
    from backend.app.hasn_plan.model.habit import Habit
    from backend.app.hasn_plan.model.plan import Plan
    from backend.app.hasn_plan.model.todo import Todo

    lines: list[str] = []
    for label, model in (('goal', Goal), ('plan', Plan), ('todo', Todo), ('event', Event), ('habit', Habit)):
        rows = (await db.execute(sa.select(model.id, model.updated_time).where(model.owner_hasn_id == owner_id))).all()
        lines.extend(f'{label}:{row_id}@{updated.isoformat() if updated else ""}' for row_id, updated in rows)
    if not lines:
        return EMPTY_PLAN_REVISION
    return hashlib.sha256('\n'.join(sorted(lines)).encode('utf-8')).hexdigest()[:16]


async def compute_owner_suppressed_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 的抑制箱指纹：sha256(sorted "message_id@resolved_at" 行)[:16]。

    聚合该 owner 名下 visible_to_owner 的抑制记录，任一被建（新门控/可达性暂留）或解除
    （resolved_at 落值）→ 集合内某行指纹变 → 整体指纹变。仅作 invalidate 帧的 ``revision``
    字段：daemon 不据此去重、收到即拉该 owner 的抑制箱镜像。
    """
    from backend.app.hasn.model.hasn_suppressed_messages import HasnSuppressedMessages

    rows = (
        await db.execute(
            sa.select(HasnSuppressedMessages.message_id, HasnSuppressedMessages.resolved_at).where(
                HasnSuppressedMessages.owner_id == owner_id,
                HasnSuppressedMessages.visible_to_owner.is_(True),
            )
        )
    ).all()
    lines = sorted(f'{mid}@{resolved.isoformat() if resolved else ""}' for mid, resolved in rows)
    if not lines:
        return EMPTY_SUPPRESSED_REVISION
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def compute_owner_notification_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 的通知中心指纹：sha256(sorted "id@state@updated_time" 行)[:16]（NOTIFUX-3）。

    聚合该 owner（``target_id``）名下全部通知，任一被建 / 已读 / 更新（``updated_time`` 变）
    → 集合内某行指纹变 → 整体指纹变。仅作 invalidate 帧的 ``revision`` 字段：daemon 不据此
    去重、收到即拉该 owner 的未读通知做增量 diff（见 daemon ``reconcile_new_notifications``）。
    """
    from backend.app.hasn.model.hasn_notifications import HasnNotifications

    rows = (
        await db.execute(
            sa.select(
                HasnNotifications.id,
                HasnNotifications.state,
                HasnNotifications.updated_time,
            ).where(HasnNotifications.target_id == owner_id)
        )
    ).all()
    lines = sorted(f'{nid}@{state}@{updated.isoformat() if updated else ""}' for nid, state, updated in rows)
    if not lines:
        return EMPTY_NOTIFICATION_REVISION
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def compute_owner_agents_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 名下 Agent 画像指纹：sha256(sorted "hasn_id@profile_revision" 行)[:16]。

    聚合该 owner 名下全部 Agent，任一 Agent 的 ``profile_revision`` 变（owner_memory 合并下发
    覆盖 ``user_md`` + bump ``profile_revision``；或单 Agent 资料编辑）或增删行 → 集合内某行指纹变
    → 整体指纹变。仅作 invalidate 帧的 ``revision`` 字段：daemon 不据此去重、收到即全量重拉该
    owner 的 agents 镜像并把新 USER.md 下发在线 runtime。
    """
    from backend.app.hasn.model.hasn_agents import HasnAgents

    rows = (
        await db.execute(
            sa.select(HasnAgents.hasn_id, HasnAgents.profile_revision).where(HasnAgents.owner_id == owner_id)
        )
    ).all()
    lines = sorted(f'{hasn_id}@{revision}' for hasn_id, revision in rows if hasn_id)
    if not lines:
        return EMPTY_AGENTS_REVISION
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def compute_owner_scopes_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 名下 Agent 三态授权指纹：sha256(sorted "agent_hasn_id@updated_time" 行)[:16]。

    聚合该 owner 名下全部 Agent 的 ``hasn_agent_scopes`` 行。任一 Agent 的 ``default_mode`` /
    ``capability_modes`` 被写（权限页改三态 / 审批「总是允许」写穿都会 ``UPDATE ... updated_time=NOW()``）
    或增删行 → 集合内某行的 ``updated_time`` 变 → 整体指纹变。仅作 invalidate 帧的 ``revision``
    字段：daemon 不据此去重、收到即刷新该 owner 名下 Agent 的 CapabilityModeMirror（本地镜像与
    云端权威对齐）。owner 隔离按 ``owner_hasn_id`` 列。
    """
    rows = (
        await db.execute(
            sa.text(
                """
                SELECT agent_hasn_id, updated_time
                FROM hasn_agent_scopes
                WHERE owner_hasn_id = :owner_id
                """
            ),
            {'owner_id': owner_id},
        )
    ).all()
    lines = sorted(f'{agent_hasn_id}@{updated_time}' for agent_hasn_id, updated_time in rows if agent_hasn_id)
    if not lines:
        return EMPTY_SCOPES_REVISION
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def compute_owner_community_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 的社区内容指纹：sha256(sorted "post:{id}@{updated}" ∪ "article:{id}@{updated}")[:16]。

    聚合该 owner 责任主体（``owner_hasn_id``——本人发帖 = 本人，分身发帖 = 主人）名下的
    帖子与文章行，任一被建/改（含审核状态迁移落 ``updated_time``）→ 集合内某行指纹变 →
    整体指纹变。评论/点赞等互动不进指纹——仅作 invalidate 帧的 ``revision`` 字段，daemon
    不据此去重、收到即 nudge webui 重拉，弱指纹不损正确性。
    """
    from backend.app.hasn_community.model import HasnArticles, HasnPosts

    lines: list[str] = []
    for label, model, id_col in (
        ('post', HasnPosts, HasnPosts.post_id),
        ('article', HasnArticles, HasnArticles.article_id),
    ):
        rows = (await db.execute(sa.select(id_col, model.updated_time).where(model.owner_hasn_id == owner_id))).all()
        lines.extend(f'{label}:{row_id}@{updated.isoformat() if updated else ""}' for row_id, updated in rows)
    if not lines:
        return EMPTY_COMMUNITY_REVISION
    return hashlib.sha256('\n'.join(sorted(lines)).encode('utf-8')).hexdigest()[:16]


async def compute_owner_decks_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 的 deck 指纹：sha256(sorted "deck:{id}@{rev}" 行)[:16]。

    ``rev`` 是 deck 的服务端单调版本（乐观并发 + 同步水位），任一 deck 被建/改（含写页/
    改大纲——service 写点均 bump ``rev``）或软删落出集合 → 整体指纹变。仅作 invalidate 帧
    的 ``revision`` 字段：daemon 不据此去重、收到即 nudge webui 重拉。
    """
    from backend.app.hasn_deck.model.deck import Deck

    rows = (
        await db.execute(sa.select(Deck.id, Deck.rev).where(Deck.owner_id == owner_id, Deck.deleted_time.is_(None)))
    ).all()
    lines = sorted(f'deck:{deck_id}@{rev}' for deck_id, rev in rows)
    if not lines:
        return EMPTY_DECKS_REVISION
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def compute_owner_groups_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 参与的群指纹：sha256(sorted "g:{gid}@{policy}@{count}@{allow}" 行)[:16]。

    「参与」= owner 本人（human hasn_id）或其名下任一 agent 是群成员。指纹字段覆盖
    ``agent_policy``（群主设置）+ ``member_count``（加/减成员即变，含加/减分身翻转 effective）+
    ``allow_member_invite_agent``（拉分身开关）。任一变 → 帧 revision 变 → daemon nudge webui 重拉
    群详情、刷新「生效发言规则」徽标。**不含** charter（隐私：准则仅主人可见，不进群级广播）。
    """
    from backend.app.hasn.model.hasn_agents import HasnAgents
    from backend.app.hasn.model.hasn_conversations import HasnConversations
    from backend.app.hasn.model.hasn_group_members import HasnGroupMembers

    # owner 本人 hasn_id + 名下 agent 的 hasn_ids
    agent_ids = (await db.execute(sa.select(HasnAgents.hasn_id).where(HasnAgents.owner_id == owner_id))).scalars().all()
    member_ids = [owner_id, *[a for a in agent_ids if a]]
    conv_ids = (
        (
            await db.execute(
                sa.select(HasnGroupMembers.conversation_id.distinct()).where(HasnGroupMembers.member_id.in_(member_ids))
            )
        )
        .scalars()
        .all()
    )
    if not conv_ids:
        return EMPTY_GROUPS_REVISION
    rows = (
        await db.execute(
            sa.select(
                HasnConversations.group_id,
                HasnConversations.agent_policy,
                HasnConversations.member_count,
                HasnConversations.allow_member_invite_agent,
            ).where(
                HasnConversations.id.in_(list(conv_ids)),
                HasnConversations.type == 'group',
                HasnConversations.status == 'active',
            )
        )
    ).all()
    lines = sorted(f'{gid}@{pol}@{cnt}@{int(bool(allow))}' for gid, pol, cnt, allow in rows)
    if not lines:
        return EMPTY_GROUPS_REVISION
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def compute_owner_billing_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 的商业化状态指纹：sha256(sorted 订阅行 ∪ 权益行)[:16]（实施/92 MK-5）。

    聚合三个权威源，任一变即整体指纹变：
    - 订阅（``user_subscription``，经 ``hasn_humans`` 把 owner hasn_id 映射到 ``user_id``）：
      指纹字段 ``tier@status@subscription_end_date``——升降级、过期、续费（end_date 前移）均变。
    - 权益（``hasn_app_entitlement``，``subject_type='owner'`` 且 ``subject_id=owner_id``）：
      指纹字段 ``app_id@status@expires_at``——授予/到期/撤销（status 迁移）、新购（增行）均变。
    - **履约事件计数**（``credit_grant_event``，doc94B M3 补入）：只取「已成功事件数 + 最新一条
      的 id」。**为什么非补不可**：doc94 之后钱包余额搬去了 NewAPI，买积分包不改订阅行也不改权益行
      ——原来的两源指纹**纹丝不动**，daemon 的 ``invalidate_resource`` 会按「revision 未变」
      把这次失效当重复推送丢掉，用户买完积分要等下一次机会刷新才看得到到账。
      不把余额本身放进指纹：余额在 NewAPI，云端读它既慢又会把展示读变成一次跨服务调用；
      「成功事件数」已经能唯一标识「又有一笔到账了」。

    仅作 invalidate 帧的 ``revision`` 字段：daemon 不据此去重、收到即拉该 owner 的费用账单中心
    镜像。空（无订阅无权益）返回稳定占位指纹。owner 隔离靠 ``user_id`` / ``subject_id`` 两列。
    """
    from backend.app.billing.model.user_subscription import UserSubscription
    from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
    from backend.app.hasn.model.hasn_humans import HasnHumans

    lines: list[str] = []

    # owner hasn_id → user_id（订阅表按 user_id 归属）
    user_id = (await db.execute(sa.select(HasnHumans.user_id).where(HasnHumans.hasn_id == owner_id))).scalars().first()
    if user_id:
        sub_rows = (
            await db.execute(
                sa.select(
                    UserSubscription.tier,
                    UserSubscription.status,
                    UserSubscription.subscription_end_date,
                ).where(UserSubscription.user_id == user_id)
            )
        ).all()
        lines.extend(f'sub:{tier}@{status}@{end.isoformat() if end else ""}' for tier, status, end in sub_rows)

    # owner 名下权益（个人主体）
    ent_rows = (
        await db.execute(
            sa.select(
                HasnAppEntitlement.app_id,
                HasnAppEntitlement.status,
                HasnAppEntitlement.expires_at,
            ).where(
                HasnAppEntitlement.subject_type == 'owner',
                HasnAppEntitlement.subject_id == owner_id,
            )
        )
    ).all()
    lines.extend(f'ent:{app_id}@{status}@{exp.isoformat() if exp else ""}' for app_id, status, exp in ent_rows)

    # 履约事件：买积分包/赠送到账不改订阅也不改权益，靠这一行让指纹动起来（doc94B M3）
    if user_id:
        from backend.app.billing.model.credit_grant_event import CreditGrantEvent

        grant_row = (
            await db.execute(
                sa.select(
                    sa.func.count(CreditGrantEvent.id),
                    sa.func.max(CreditGrantEvent.id),
                ).where(
                    CreditGrantEvent.user_id == user_id,
                    CreditGrantEvent.status == 'succeeded',
                )
            )
        ).one_or_none()
        if grant_row is not None:
            grant_count, latest_grant_id = grant_row
            lines.append(f'grant:{int(grant_count or 0)}@{int(latest_grant_id or 0)}')

    if not lines:
        return EMPTY_BILLING_REVISION
    return hashlib.sha256('\n'.join(sorted(lines)).encode('utf-8')).hexdigest()[:16]


async def compute_owner_project_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 的平台项目指纹：sha256(sorted "project:{id}@{updated_time}" 行)[:16]（doc38 U5）。

    聚合该 owner 名下全部平台项目（``hasn_project``），任一被建/改（含归档 status→archived 落
    ``updated_time``）或增删行 → 集合内某行指纹变 → 整体指纹变。仅作 invalidate 帧的 ``revision``
    字段：daemon 不据此去重、收到即拉该 owner 的项目镜像。owner 隔离按 ``owner_id`` 列。
    """
    from backend.app.hasn_project.model import HasnProject

    rows = (
        await db.execute(sa.select(HasnProject.id, HasnProject.updated_time).where(HasnProject.owner_id == owner_id))
    ).all()
    lines = sorted(f'project:{row_id}@{updated.isoformat() if updated else ""}' for row_id, updated in rows)
    if not lines:
        return EMPTY_PROJECT_REVISION
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def compute_owner_finance_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 金融七表指纹：sha256(sorted "kind:id@revision@status" 行)[:16]。

    六类产物和 watchlist 的有效 create/update/delete 都会改变成员或单调 ``revision``；
    tombstone 仍保留在集合中并带 ``status='deleted'``，因此删除也必然改变指纹并触发跨设备下行。
    """
    from backend.app.hasn_finance.model.backtest_report import BacktestReport
    from backend.app.hasn_finance.model.research_report import ResearchReport
    from backend.app.hasn_finance.model.shadow_account import ShadowAccount
    from backend.app.hasn_finance.model.strategy import Strategy
    from backend.app.hasn_finance.model.trade_review import TradeReview
    from backend.app.hasn_finance.model.watch_briefing import WatchBriefing
    from backend.app.hasn_finance.model.watchlist import Watchlist

    lines: list[str] = []
    for label, model in (
        ('research', ResearchReport),
        ('strategy', Strategy),
        ('backtest', BacktestReport),
        ('review', TradeReview),
        ('shadow', ShadowAccount),
        ('briefing', WatchBriefing),
        ('watchlist', Watchlist),
    ):
        rows = (
            await db.execute(
                sa.select(model.id, model.revision, model.status).where(model.owner_id == owner_id)
            )
        ).all()
        lines.extend(f'{label}:{row_id}@{revision}@{status}' for row_id, revision, status in rows)
    if not lines:
        return EMPTY_FINANCE_REVISION
    return hashlib.sha256('\n'.join(sorted(lines)).encode('utf-8')).hexdigest()[:16]


async def compute_owner_memory_revision(db: AsyncSession, owner_id: str) -> str:
    """某 owner 记忆命名空间指纹：sha256(sorted "namespace@revision" 行)[:16]。

    聚合该 owner scope（sync_scope_kind='owner'、sync_scope_id=owner_id）下全部记忆命名空间的
    权威 revision（``hasn_memory.namespace_revision``）：任一命名空间 revision 前进（如 peer 画像
    合成 bump portraits）→ 集合指纹变 → invalidate 帧 revision 变。daemon 不据此去重、收到即
    触发 memory sync_pull 拉取该 owner 全部记忆命名空间落本地镜像。空则返回稳定占位指纹。
    """
    rows = (
        await db.execute(
            sa.text(
                """
                SELECT namespace, revision
                FROM hasn_memory.namespace_revision
                WHERE sync_scope_kind = 'owner' AND sync_scope_id = :owner_id
                """
            ),
            {'owner_id': owner_id},
        )
    ).all()
    lines = sorted(f'{namespace}@{revision}' for namespace, revision in rows if namespace)
    if not lines:
        return '0' * 16
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


async def _compute_revision(kind: str, db: AsyncSession) -> str:
    """按 kind 重算权威 revision（直接读各自数据源，不读缓存）。"""
    if kind == KIND_BUILTIN_CATALOG:
        return await compute_builtin_catalog_revision(db)
    if kind == KIND_COMMON_SKILLS:
        from backend.app.marketplace.service.common_skills_service import (
            get_common_skill_snapshot,
        )

        _, rev = await get_common_skill_snapshot(db)
        return rev
    if kind == KIND_PLATFORM_CONFIG:
        from backend.app.hasn.service.platform_default_config_service import (
            platform_default_config_service,
        )

        _, rev = await platform_default_config_service.get_effective_config(db)
        return rev
    if kind == KIND_DESIGNSYSTEM:
        return await compute_designsystem_revision(db)
    if kind == KIND_SPEECH_CATALOG:
        from backend.app.hasn.service.speech_catalog_service import speech_catalog_service

        return await speech_catalog_service.get_revision(db)
    raise ValueError(f'unknown sync kind: {kind}')


async def get_all_revisions(db: AsyncSession) -> dict[str, str]:
    """连接握手用：返回 ``{kind: revision}``。读 Redis 缓存，miss 即重算并回填（cheap）。

    redis 不可用 → 退化为每次重算（仍返回正确 revision，只是不省那几次查询）。
    """
    out: dict[str, str] = {}
    for kind in KINDS:
        cached = None
        try:
            cached = await redis_client.get(f'{REV_PREFIX}:{kind}')
        except Exception as exc:  # redis 故障退化为重算
            logger.warning('[sync] read revision cache failed kind=%s: %s', kind, exc)
        if cached:
            out[kind] = cached
            continue
        rev = await _compute_revision(kind, db)
        out[kind] = rev
        try:
            await redis_client.set(f'{REV_PREFIX}:{kind}', rev, ex=REV_TTL_SECS)
        except Exception as exc:
            logger.warning('[sync] write revision cache failed kind=%s: %s', kind, exc)
    return out


async def bump(kind: str, db: AsyncSession, *, owner_id: str | None = None) -> str:
    """写点变更后调用：重算 revision → 写缓存 → push ``hasn.sync.invalidate`` 给在线节点。

    - ``owner_id=None`` → 全局广播（全部在线节点）；指定 → 仅推该 owner 的在线节点。
    - 返回新 revision。push 失败不抛（best-effort）：离线节点靠重连握手对账追平，
      写点（如 admin PUT）绝不能因推送失败而失败。
    """
    if kind not in KINDS:
        raise ValueError(f'unknown sync kind: {kind}')
    rev = await _compute_revision(kind, db)

    # 公共技能写点 → 即时触发共享目录 reconcile（doc11 §6 B3；beat 每 20 分钟另有兜底）。
    # best-effort：celery broker 不可用绝不拖垮写点主流程（下轮 beat 自会追平）。
    if kind == KIND_COMMON_SKILLS:
        try:
            from backend.app.marketplace.tasks import marketplace_shared_skills_reconcile

            marketplace_shared_skills_reconcile.delay()
        except Exception as exc:
            logger.warning('[sync] enqueue shared skills reconcile failed (non-fatal): %s', exc)

    try:
        await redis_client.set(f'{REV_PREFIX}:{kind}', rev, ex=REV_TTL_SECS)
    except Exception as exc:
        logger.warning('[sync] cache revision failed kind=%s: %s', kind, exc)

    try:
        pushed = await _get_node_session_gateway().broadcast_sync_invalidate(kind, rev, owner_id=owner_id)
        logger.info(
            '[sync] invalidate kind=%s rev=%s pushed=%d owner=%s',
            kind,
            rev,
            pushed,
            owner_id or '*',
        )
    except Exception as exc:  # 推送 best-effort，不拖垮写点
        logger.warning('[sync] broadcast invalidate failed kind=%s: %s', kind, exc)
    return rev


async def bump_owner(kind: str, db: AsyncSession, owner_id: str) -> str:
    """owner 定向写点（如任务变更）：重算该 owner 维度 revision → 仅 push 给该 owner 在线节点。

    与全局 ``bump`` 的差异：
      - revision 是 per-owner 维度，**不写全局 Redis revision 缓存**（无全局键），
        **不进 get_all_revisions 握手快照**（per-owner 指纹对全局握手无意义；离线追平靠
        该 owner 任务镜像的周期 ``sync_pull``）。
      - push best-effort，不抛：失败不拖垮写点，靠周期 sync_pull 兜底追平。

    返回新 revision。
    """
    if kind not in OWNER_KINDS:
        raise ValueError(f'unknown owner sync kind: {kind}')
    if kind == KIND_TASKS:
        rev = await compute_owner_tasks_revision(db, owner_id)
    elif kind == KIND_PLAN:
        rev = await compute_owner_plan_revision(db, owner_id)
    elif kind == KIND_SUPPRESSED:
        rev = await compute_owner_suppressed_revision(db, owner_id)
    elif kind == KIND_AGENTS:
        rev = await compute_owner_agents_revision(db, owner_id)
    elif kind == KIND_MEMORY:
        rev = await compute_owner_memory_revision(db, owner_id)
    elif kind == KIND_COMMUNITY:
        rev = await compute_owner_community_revision(db, owner_id)
    elif kind == KIND_DECKS:
        rev = await compute_owner_decks_revision(db, owner_id)
    elif kind == KIND_SCOPES:
        rev = await compute_owner_scopes_revision(db, owner_id)
    elif kind == KIND_NOTIFICATION:
        rev = await compute_owner_notification_revision(db, owner_id)
    elif kind == KIND_GROUPS:
        rev = await compute_owner_groups_revision(db, owner_id)
    elif kind == KIND_BILLING:
        rev = await compute_owner_billing_revision(db, owner_id)
    elif kind == KIND_PROJECT:
        rev = await compute_owner_project_revision(db, owner_id)
    elif kind == KIND_FINANCE:
        rev = await compute_owner_finance_revision(db, owner_id)
    elif kind == KIND_CONTACTS:
        # presence 不进表指纹，固定 revision（见 CONTACTS_PRESENCE_REVISION 注释）。
        rev = CONTACTS_PRESENCE_REVISION
    else:  # pragma: no cover - 新增 owner kind 须在此补分支
        raise ValueError(f'unsupported owner sync kind: {kind}')

    try:
        pushed = await _get_node_session_gateway().broadcast_sync_invalidate(kind, rev, owner_id=owner_id)
        logger.info('[sync] invalidate kind=%s rev=%s pushed=%d owner=%s', kind, rev, pushed, owner_id)
    except Exception as exc:  # 推送 best-effort，不拖垮写点
        logger.warning('[sync] broadcast invalidate failed kind=%s owner=%s: %s', kind, owner_id, exc)
    return rev
