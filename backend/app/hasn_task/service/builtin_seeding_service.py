"""内置 agent 创建 + 内置任务播种（INSERT-only）+ 官方更新检测/手动更新。

设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/08-内置定时任务体系与内置Agent设计.md
（§5 内置 agent 注册时建齐 / §6.1 播种算法 INSERT-only / §6.6 官方更新用户手动决策）

核心心智：
- hasn_task.task = 所有任务唯一权威；内置任务 = created_by_kind='builtin' + builtin_key 的普通行。
- 自动播种 INSERT-only：(owner_id, builtin_key) 已存在则跳过，系统绝不自动 UPDATE/覆盖。
- 官方更新不自动下发；以「可更新」提示暴露，由用户在桌面端手动触发 update_builtin_task_from_catalog。
"""

import uuid

import sqlalchemy as sa

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnAgents, HasnHumans
from backend.app.hasn.schema.hasn_sync import ClientEvent
from backend.app.hasn.service.hasn_sync_service import hasn_sync_service
from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog
from backend.app.hasn_task.model.task import HasnTask
from backend.app.hasn_task.service.task_service import calc_next_run_at
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.common.log import log
from backend.utils.timezone import timezone

# 主脑模板键：reconcile 跳过它（由 onboarding.ensure_default_agent 负责创建主脑）。
PRIMARY_BUILTIN_KEY = 'assistant'

# 内置任务的服务端写入节点标识：经 save_task_event 走权威 upsert + hasn_sync_events feed，
# 内置任务因此与用户任务无差别地经普通 task sync 通道下行到各节点（设计 §6.2）。
CLOUD_BUILTIN_NODE_ID = 'cloud-builtin-seed'


def _briefing_time_to_cron(hhmm: str | None) -> str | None:
    """'08:00' → '0 8 * * *'（与 daemon briefing_time_to_cron 同口径）。非法返回 None。"""
    if not hhmm or ':' not in hhmm:
        return None
    h, _, m = hhmm.partition(':')
    if len(h) != 2 or len(m) != 2:
        return None
    try:
        hour, minute = int(h), int(m)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f'{minute} {hour} * * *'


def _split_csv(value: str | None) -> list[str]:
    return [s.strip() for s in (value or '').split(',') if s.strip()]


async def _active_agents(db: AsyncSession, owner_id: str) -> list[HasnAgents]:
    rows = await db.execute(
        sa
        .select(HasnAgents)
        .where(HasnAgents.owner_id == owner_id, HasnAgents.status == 'active')
        .order_by(HasnAgents.id.asc())
    )
    return list(rows.scalars().all())


async def _resolve_primary_agent(db: AsyncSession, owner_id: str, agents: list[HasnAgents]) -> HasnAgents | None:
    """主脑：优先 workbench_pref.primary_agent_id；空则回落 role='primary' 最早活跃分身；再回落首个活跃分身。"""
    pref_pid = (
        await db.execute(
            sa.select(HasnOwnerWorkbenchPref.primary_agent_id).where(HasnOwnerWorkbenchPref.owner_hasn_id == owner_id)
        )
    ).scalar_one_or_none()
    by_id = {a.hasn_id: a for a in agents}
    if pref_pid and pref_pid in by_id:
        return by_id[pref_pid]
    for a in agents:
        if a.role == 'primary':
            return a
    return agents[0] if agents else None


async def reconcile_builtin_agents(db: AsyncSession, owner_id: str, node_id: str | None = None) -> list[str]:
    """建齐所有 builtin=true 模板的分身（幂等按 builtin_agent_key；best-effort，单个失败不阻断）。

    主脑（assistant）由 onboarding.ensure_default_agent 创建并已 stamp builtin_agent_key='assistant'，
    此处显式跳过，仅创建 role='specialist' 的专业内置分身。
    """
    from backend.app.marketplace.model.marketplace_template import MarketplaceTemplate

    templates = (
        (
            await db.execute(
                sa.select(MarketplaceTemplate).where(
                    MarketplaceTemplate.builtin.is_(True),
                    MarketplaceTemplate.template_type == 'agent_template',
                    MarketplaceTemplate.builtin_key.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not templates:
        return []

    # 已建齐的 builtin_agent_key 集合（幂等判存在）。
    existing_keys = set(
        (
            await db.execute(
                sa.select(HasnAgents.builtin_agent_key).where(
                    HasnAgents.owner_id == owner_id,
                    HasnAgents.builtin_agent_key.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    owner_nickname = (
        await db.execute(sa.select(HasnHumans.nickname).where(HasnHumans.hasn_id == owner_id))
    ).scalar_one_or_none()

    created: list[str] = []
    for tpl in templates:
        key = tpl.builtin_key
        if key == PRIMARY_BUILTIN_KEY:
            continue  # 主脑由 ensure_default_agent 负责
        if key in existing_keys:
            continue  # 已建，跳过（不 clobber 用户改名/人设）

        try:
            from backend.app.hasn.service import agent_avatar_service
            from backend.app.hasn.service.hasn_agents_service import agent_profile_service
            from backend.app.hasn.service.hasn_auth import register_hasn_agent

            version = await _latest_template_version(db, tpl.template_id)
            # display_name 现为 `{主人昵称}的{专家名称}`（如「小智的内容运营官」），专家名取 tpl.name；
            # 主人未设昵称时先以纯专家名占位，设昵称后由 refresh_seeded_agent_display_names 刷新。
            display_name = await agent_profile_service.gateway.resolve_default_agent_display_name(
                db, profession=tpl.name or None, owner_nickname=owner_nickname
            )
            # 头像：每个内置分身一张确定性几何头像（seed=owner:key，每人+每内置分身各不同、无限不重复），
            # 生成/落桶失败回退模板 icon（best-effort，绝不阻断播种）。
            avatar = await agent_avatar_service.resolve_generated_avatar_url(db, f'{owner_id}:{key}') or (
                tpl.icon_url or None
            )
            await register_hasn_agent(
                db=db,
                owner_hasn_id=owner_id,
                agent_name=key,  # owner 内唯一、稳定的 slug
                display_name=display_name,  # 全局唯一
                role='specialist',
                builtin_agent_key=key,  # ★ 匹配纽带
                profession=tpl.name or None,
                avatar=avatar,
                agent_type='cloud',
                node_id=node_id,
                description=tpl.description or None,
                template_id=tpl.template_id,
                template_version=version,
                skills=_split_csv(tpl.skill_dependencies) or None,
                soul_md=tpl.soul_md,
                agents_md=tpl.agents_md,
                user_md=tpl.user_md,
                memory_md=getattr(tpl, 'memory_md', None),
                created_via='onboarding',
            )
            existing_keys.add(key)
            created.append(key)
        except Exception as exc:
            log.warning('reconcile_builtin_agents: failed to create builtin agent {}: {!r}', key, exc)
    return created


async def _latest_template_version(db: AsyncSession, template_id: str) -> str | None:
    """模板最新版本号（is_latest 优先，回落最近创建）；缺失返回 None（非必填元数据）。"""
    from backend.app.marketplace.model.marketplace_template_version import MarketplaceTemplateVersion

    return (
        await db.execute(
            sa
            .select(MarketplaceTemplateVersion.version)
            .where(MarketplaceTemplateVersion.template_id == template_id)
            .order_by(
                MarketplaceTemplateVersion.is_latest.desc(),
                MarketplaceTemplateVersion.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


def _builtin_prompt(item: HasnBuiltinTaskCatalog) -> str:
    """内置任务触发 prompt（系统提示词承载真正约束，prompt 仅作触发轮，与 daemon trigger_prompt 同口径）。"""
    return item.description or f'执行官方内置任务：{item.name}'


def _builtin_skill_bundle_ids(item: HasnBuiltinTaskCatalog) -> list[str]:
    return [item.skill_bundle] if item.skill_bundle else []


async def seed_builtin_tasks(db: AsyncSession, owner_id: str) -> list[str]:
    """INSERT-only 播种内置任务进 hasn_task.task。

    - 播种「全部」内置条目（**不**按 catalog.enabled 过滤）：内置任务必须人人都有，
      只是初始启停态不同（daily_briefing 默认开、其余默认停，用户手动启用即可）。
      `enabled` 是「官方上/下线」开关（仅影响《可更新》提示、refresh-builtin 可用性），
      绝不 gate 播种——早期 `where enabled=true` 是 M5 之前「daemon 本地物化」旧模型的遗留过滤，
      退役本地物化、改走「真实 task 行 + 普通同步」后此过滤导致 enabled=false 的条目永不播种（bug）。
    - 解析承接分身：target_agent_type 匹配的内置 agent（只取一个，最早创建）；无则回退主脑。
    - (owner_id, builtin_key) 已存在存活行 → 跳过（INSERT-only 铁律，绝不自动覆盖用户修改）。
    - 初始 enabled = catalog.default_enabled（之后归用户）；daily_briefing 特例沿用 owner 简报偏好。
    """
    catalog = (await db.execute(sa.select(HasnBuiltinTaskCatalog))).scalars().all()
    if not catalog:
        return []

    agents = await _active_agents(db, owner_id)
    by_key: dict[str, HasnAgents] = {}
    for a in agents:
        if a.builtin_agent_key and a.builtin_agent_key not in by_key:
            by_key[a.builtin_agent_key] = a  # 最早创建（已按 id asc 排序）
    primary = await _resolve_primary_agent(db, owner_id, agents)

    # daily_briefing 特例：沿用 owner 简报时刻/开关作初始值（§6.2）。
    pref = (
        await db.execute(sa.select(HasnOwnerWorkbenchPref).where(HasnOwnerWorkbenchPref.owner_hasn_id == owner_id))
    ).scalar_one_or_none()

    seeded: list[str] = []
    for item in catalog:
        # 0) 已播种则跳过（INSERT-only）
        exists = (
            await db.execute(
                sa.select(HasnTask.id).where(
                    HasnTask.owner_id == owner_id,
                    HasnTask.builtin_key == item.builtin_key,
                    HasnTask.deleted_at.is_(None),
                )
            )
        ).first()
        if exists:
            continue

        # 1) 解析承接分身：按类型匹配，回退主脑
        target = by_key.get(item.target_agent_type) if item.target_agent_type else None
        target = target or primary
        if target is None:
            continue  # 极端：无任何活跃分身 → 本轮不播种，留待下次

        schedule_config = dict(item.schedule_config or {})
        enabled = bool(item.default_enabled)
        if item.builtin_key == 'daily_briefing' and pref is not None:
            cron = _briefing_time_to_cron(pref.briefing_time)
            if cron:
                schedule_config = {'expr': cron}
            enabled = bool(pref.briefing_enabled)

        # 2) 经权威 save_task_event 写入：authoritative upsert + revision + hasn_sync_events feed
        #    → 内置任务走普通 task sync 通道下行到本地（§6.2），无专属物化逻辑。
        now = timezone.now()
        next_run_at = calc_next_run_at(item.schedule_type, schedule_config)
        payload = {
            'task_id': str(uuid.uuid4()),
            'agent_id': target.hasn_id,
            'name': item.name,
            'description': item.description,
            'prompt': _builtin_prompt(item),
            'system_prompt': item.system_prompt,
            'skill_bundle_ids': _builtin_skill_bundle_ids(item),
            'schedule_type': item.schedule_type,
            'schedule_config': schedule_config,
            'enabled': enabled,
            'state': 'scheduled',
            'next_run_at': next_run_at.isoformat() if next_run_at else None,
            'created_by_kind': 'builtin',
            'builtin_key': item.builtin_key,
            'builtin_synced_revision': int(item.revision or 0),
            'created_at': now.isoformat(),
            'updated_at': now.isoformat(),
        }
        # client_event_id 稳定到 (owner, builtin_key)：重跑天然幂等（短路返回已存在 revision），
        # 与 INSERT-only 铁律一致；并发兜底仍靠 (owner_id, builtin_key) 唯一索引。
        event = ClientEvent(
            client_event_id=f'bts_{owner_id}_{item.builtin_key}',
            event_type='task.created',
            payload={'task': payload},
            hasn_id=target.hasn_id,
        )
        try:
            async with db.begin_nested():  # SAVEPOINT：唯一键并发撞车只回滚这一行
                await hasn_sync_service.gateway.save_task_event(
                    db, owner_id=owner_id, node_id=CLOUD_BUILTIN_NODE_ID, event=event
                )
            seeded.append(item.builtin_key)
        except IntegrityError:
            # (owner_id, builtin_key) 唯一索引撞车 → 当作「已存在」吞掉。
            log.info('seed_builtin_tasks: {} already seeded for {} (race), skip', item.builtin_key, owner_id)
    return seeded


def builtin_update_available(item: HasnBuiltinTaskCatalog | None, synced_revision: int | None) -> bool:
    """可更新检测（读时派生，不落库）：catalog 在线且 revision > 已同步 revision。"""
    if item is None or not item.enabled:
        return False
    return int(item.revision or 0) > int(synced_revision or 0)


async def load_builtin_catalog_map(db: AsyncSession) -> dict[str, HasnBuiltinTaskCatalog]:
    """builtin_key → catalog 条目映射（读路径派生「可更新」用，一次查询全量目录）。"""
    rows = (await db.execute(sa.select(HasnBuiltinTaskCatalog))).scalars().all()
    return {row.builtin_key: row for row in rows if row.builtin_key}


async def update_builtin_task_from_catalog(db: AsyncSession, owner_id: str, task_uuid: str) -> HasnTask:
    """官方更新：用户手动决策（§6.6）。绝不被自动路径调用。

    应用官方定义字段（name/description/prompt/system_prompt/schedule/skill_bundle），
    保留用户偏好（enabled 启停 + agent_id 绑定），追平 builtin_synced_revision，task_revision+1 触发下行。
    """
    from backend.common.exception import errors

    task = (
        await db.execute(
            sa.select(HasnTask).where(
                HasnTask.owner_id == owner_id,
                HasnTask.task_uuid == task_uuid,
                HasnTask.created_by_kind == 'builtin',
                HasnTask.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if task is None or not task.builtin_key:
        raise errors.NotFoundError(msg='内置任务不存在或不可更新')

    item = (
        await db.execute(
            sa.select(HasnBuiltinTaskCatalog).where(HasnBuiltinTaskCatalog.builtin_key == task.builtin_key)
        )
    ).scalar_one_or_none()
    if item is None or not item.enabled:
        raise errors.ConflictError(msg='官方目录已下线，暂无可更新版本')

    # 应用官方定义（用户主动选择 = 接受官方覆盖这些字段），但保留用户偏好：
    #   enabled（启停）、agent_id（绑定）不动；追平 builtin_synced_revision → 「可更新」消失。
    # 经权威 save_task_event 走 task.updated：upsert(task_revision+1) + sync feed → 普通同步下行。
    now = timezone.now()
    schedule_config = dict(item.schedule_config or {})
    next_run_at = calc_next_run_at(item.schedule_type, schedule_config)
    state = task.state if task.state != 'deleted' else 'scheduled'
    payload = {
        'task_id': task.task_uuid,
        'base_revision': task.task_revision,  # 防丢更新：等于当前 → 不判 stale
        'agent_id': task.agent_id,  # 保留绑定
        'name': item.name,
        'description': item.description,
        'prompt': _builtin_prompt(item),
        'system_prompt': item.system_prompt,
        'skill_bundle_ids': _builtin_skill_bundle_ids(item),
        'schedule_type': item.schedule_type,
        'schedule_config': schedule_config,
        'enabled': bool(task.enabled),  # 保留启停
        'state': state,
        'next_run_at': next_run_at.isoformat() if next_run_at else None,
        'created_by_kind': 'builtin',
        'builtin_key': task.builtin_key,
        'builtin_synced_revision': int(item.revision or 0),  # 追平官方
        'created_at': task.created_time.isoformat() if task.created_time else now.isoformat(),
        'updated_at': now.isoformat(),
    }
    event = ClientEvent(
        client_event_id=f'btu_{owner_id}_{task.builtin_key}_{int(item.revision or 0)}',
        event_type='task.updated',
        payload={'task': payload},
        hasn_id=task.agent_id,
    )
    await hasn_sync_service.gateway.save_task_event(db, owner_id=owner_id, node_id=CLOUD_BUILTIN_NODE_ID, event=event)
    await db.refresh(task)
    return task
