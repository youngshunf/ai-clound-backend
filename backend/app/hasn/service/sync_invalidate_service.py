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

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.database.redis import redis_client

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# revision 缓存键前缀
REV_PREFIX = 'hasn:sync:rev'
# 缓存 TTL（兜底：写点漏 bump 时，最长这么久后 get_all_revisions 缓存过期重算自愈）
REV_TTL_SECS = 3600

KIND_BUILTIN_CATALOG = 'builtin_catalog'
KIND_COMMON_SKILLS = 'common_skills'
KIND_PLATFORM_CONFIG = 'platform_config'
KIND_DESIGNSYSTEM = 'designsystem'
# 全局 kind：单一全局 revision，进 get_all_revisions 握手快照，bump(owner_id=None) 全局广播。
KINDS = (KIND_BUILTIN_CATALOG, KIND_COMMON_SKILLS, KIND_PLATFORM_CONFIG, KIND_DESIGNSYSTEM)

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
OWNER_KINDS = (KIND_TASKS, KIND_PLAN, KIND_SUPPRESSED, KIND_AGENTS, KIND_MEMORY)
# 某 owner 任务镜像为空时的稳定指纹
EMPTY_TASKS_REVISION = 'empty'
# 某 owner 规划数据为空时的稳定指纹（同上约定）
EMPTY_PLAN_REVISION = 'empty'
# 某 owner 抑制箱为空时的稳定指纹（同上约定）
EMPTY_SUPPRESSED_REVISION = 'empty'
# 某 owner 名下无 Agent 时的稳定指纹（同上约定）
EMPTY_AGENTS_REVISION = 'empty'

# 内置任务目录为空时的稳定指纹（对齐 common_skills 的 EMPTY 约定）
EMPTY_BUILTIN_CATALOG_REVISION = 'empty'
# 设计系统库为空时的稳定指纹（同上约定）
EMPTY_DESIGNSYSTEM_REVISION = 'empty'


async def compute_designsystem_revision(db: AsyncSession) -> str:
    """设计系统全局指纹：sha256(sorted "id@content_hash" 行)[:16]，对齐 common_skills_revision。

    任一设计系统的 ``content_hash`` 变（save 落新版）或增删 → 指纹变 → 在线节点对账各自
    owner 镜像（云端权威，节点只拉自己可见域 builtin∪owner∪共享）。软删行（``deleted_time``
    非空）落出集合 → 删一套 → 集合缩小 → 指纹变 → 镜像感知下线。
    """
    from backend.app.hasn_designsystem.model.design_system import DesignSystem

    rows = (
        await db.execute(
            sa.select(DesignSystem.id, DesignSystem.content_hash).where(DesignSystem.deleted_time.is_(None))
        )
    ).all()
    lines = sorted(f'{ds_id}@{content_hash or ""}' for ds_id, content_hash in rows)
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
    try:
        await redis_client.set(f'{REV_PREFIX}:{kind}', rev, ex=REV_TTL_SECS)
    except Exception as exc:
        logger.warning('[sync] cache revision failed kind=%s: %s', kind, exc)

    from backend.app.hasn.service.ws_router import ws_router

    try:
        pushed = await ws_router.broadcast_sync_invalidate(kind, rev, owner_id=owner_id)
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
    else:  # pragma: no cover - 新增 owner kind 须在此补分支
        raise ValueError(f'unsupported owner sync kind: {kind}')

    from backend.app.hasn.service.ws_router import ws_router

    try:
        pushed = await ws_router.broadcast_sync_invalidate(kind, rev, owner_id=owner_id)
        logger.info('[sync] invalidate kind=%s rev=%s pushed=%d owner=%s', kind, rev, pushed, owner_id)
    except Exception as exc:  # 推送 best-effort，不拖垮写点
        logger.warning('[sync] broadcast invalidate failed kind=%s owner=%s: %s', kind, owner_id, exc)
    return rev
