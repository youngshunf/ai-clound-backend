"""统一通知服务（§5）。

唯一生产入口 emit()：解析投递策略 → 去重/聚合 → 落权威行 hasn_notifications（超集）→
（P2 起）按策略 fanout 到各承载。本模块还承载读取/已读/未读 与 主人偏好 CRUD。

承袭社区 notification_service 的读时聚合与未读分类语义（提升为通用），社区 notify_* 改为
本服务 emit() 的薄封装（见 app/hasn_community/service/notification_service.py）。
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.notification.model.hasn_notification_preferences import HasnNotificationPreferences
from backend.app.notification.service.delivery_policy import default_priority, resolve_policy
from backend.common.exception import errors
from backend.common.log import log
from backend.utils.timezone import timezone as _tz

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class NotificationService:
    """统一通知服务（生产 + 读取 + 偏好）。"""

    # 限频窗口 / 阈值（§9 防通知轰炸）：同一 (recipient, source) 近窗内超过阈值，
    # 压制"吵"的承载（toast/push/card_message），但仍落 center 权威行（D1 不漏事）。
    RATE_WINDOW_SECONDS = 60
    RATE_MAX_PER_WINDOW = 20

    # ==================== 生产入口 ====================

    @classmethod
    async def emit(
        cls,
        db: AsyncSession,
        *,
        recipient_id: str,
        source: dict[str, Any],
        category: str,
        type: str,
        title: str,
        body: str | None = None,
        payload: dict[str, Any] | None = None,
        priority: str | None = None,
        dedupe_key: str | None = None,
        group_key: str | None = None,
        delivery_hint: dict[str, Any] | None = None,
    ) -> int:
        """落一条权威通知行，返回 notification_id（§5）。

        P1：解析策略 → 去重/聚合 → 落权威行（delivery 记录策略意图）。
        承载 fanout（卡片消息/toast/push）在 P2 接入；center 即权威行本身。
        """
        if not recipient_id:
            raise errors.RequestError(msg='recipient_id 不能为空')
        payload = dict(payload or {})
        priority = priority or default_priority(category)

        # 0) 分面归属守卫（doc `通知系统统一设计/01` R1/R2·纵深防御）：
        #    主人自己的分身向主人「汇报/请示」= 汇报面，不进通知中心（不落 hasn_notifications）。
        #    判据：source.kind==agent 且该分身的主人==recipient（OwnerLoopback 方向）。
        #    落点：分身主会话一条汇报卡（agent 本身即会话身份，未读挂在与该分身的会话上）。
        #    即使某 producer 漏改仍走 emit()，此守卫也保证「自分身→主人」绝不污染通知中心。
        if await cls._is_owner_loopback(db, source=source, recipient_id=recipient_id):
            from backend.app.notification.service.notification_carrier import deliver_report_card_to_owner

            return await deliver_report_card_to_owner(
                db,
                recipient_id=recipient_id,
                source=dict(source or {}),
                title=title,
                body=body,
                payload=payload,
                priority=priority,
            )

        # 1) 解析投递策略 = category 默认 ⊕ 主人偏好 ⊕ delivery_hint
        pref = await cls._get_effective_preference(db, owner_id=recipient_id, category=category)
        policy = resolve_policy(
            category=category,
            priority=priority,
            owner_pref=pref,
            delivery_hint=delivery_hint,
            now=_tz.now(),
        )

        # 2) group_key 默认 {type}:{target.id}
        if not group_key:
            target_id = (payload.get('target') or {}).get('id', '')
            group_key = f'{type}:{target_id}'

        # 3) 去重/聚合：dedupe_key 命中近窗未读行 → 聚合计数，不重复落行
        if dedupe_key:
            existing = (
                await db.execute(
                    select(HasnNotifications)
                    .where(
                        HasnNotifications.target_id == recipient_id,
                        HasnNotifications.dedupe_key == dedupe_key,
                        HasnNotifications.state == 'unread',
                    )
                    .order_by(HasnNotifications.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                merged = dict(existing.data or {})
                merged.update(payload)
                merged['aggregated_count'] = int(merged.get('aggregated_count', 1)) + 1
                existing.data = merged
                existing.title = title
                if body is not None:
                    existing.body = body
                existing.updated_time = _tz.now()
                await db.flush()
                # NOTIFUX-3：聚合更新（aggregated_count 变）也 bump owner，让 daemon 刷新 webui
                # 通知列表/未读徽标；OS 系统通知按通知 id 增量 diff、同 id 不重发，故不会重复打扰。
                await cls._bump_notification_revision(db, recipient_id)
                return existing.id

        # 3b) 限频（§9 防通知轰炸）：同一 (recipient, source) 近窗超阈值 → 压制"吵"的承载
        #     （toast/push/card_message），但仍落 center 权威行（§4.4 step4：center 不可关）。
        #     critical 不限频（安全/系统告警必达）；只对带 id 的 app/agent/external 源生效。
        if priority != 'critical':
            policy = await cls._apply_rate_limit(db, recipient_id=recipient_id, source=source, policy=policy)

        # 4) 落权威行
        row = HasnNotifications(
            target_id=recipient_id,
            type=type,
            title=title,
            body=body,
            data=payload,
            read=False,
            category=category,
            priority=priority,
            source=dict(source or {}),
            dedupe_key=dedupe_key,
            group_key=group_key,
            delivery=policy,
            state='unread',
        )
        db.add(row)
        await db.flush()

        # 4b) 卡片消息承载（§6.2）：策略允许 card_message + 来源可建服务号 →
        #     投影成 type=notification,content_type=card 消息落「服务号 ⇄ 接收方」service 会话。
        if policy.get('channels', {}).get('card_message'):
            await cls._fanout_card(db, row=row, source=dict(source or {}))

        # 4c) toast 在线投递（§5）：策略允许 toast → 写 notification.created sync_event，
        #     供 daemon 下行 sync 拉取 → 经 NotificationBus 投 OS Toast（权威策略仍在云端）。
        if policy.get('channels', {}).get('toast'):
            await cls._emit_sync_event(db, row=row)

        # 4d) NOTIFUX-3：新通知落权威行 → bump 该 owner 的通知 revision（WSPUSH KIND_NOTIFICATION）。
        #     在线节点 daemon 收到即拉未读通知、diff 出新增未读项发原生系统通知（点击深链到通知
        #     覆盖层），并 nudge webui 刷新通知列表+未读徽标。离线节点靠周期 sync_pull 兜底追平。
        await cls._bump_notification_revision(db, recipient_id)

        return row.id

    @staticmethod
    async def _bump_notification_revision(db: AsyncSession, recipient_id: str) -> None:
        """bump 该 owner 的通知 revision → push 在线节点（NOTIFUX-3·通知面系统通知的触发源）。

        best-effort：失效推送本就不该拖垮通知落库（bump_owner 内部对 push 已 best-effort，此处
        再兜一层保险，连 revision 计算异常也不外抛）。离线/推送失败时 daemon 周期 sync_pull 兜底。
        延迟 import 避免与 sync_invalidate_service 潜在的模块级循环依赖。
        """
        try:
            from backend.app.hasn.service.sync_invalidate_service import (
                KIND_NOTIFICATION,
                bump_owner,
            )

            await bump_owner(KIND_NOTIFICATION, db, recipient_id)
        except Exception:  # noqa: BLE001 - 推送 best-effort，绝不因失效推送失败而丢通知
            log.warning('[notification] bump 通知 revision 失败 owner=%s', recipient_id, exc_info=True)

    @classmethod
    async def app_emit(
        cls,
        db: AsyncSession,
        *,
        app_id: str,
        owner_hasn_id: str,
        category: str,
        type: str,
        title: str,
        body: str | None = None,
        payload: dict[str, Any] | None = None,
        priority: str | None = None,
        dedupe_key: str | None = None,
        group_key: str | None = None,
        want_card: bool = True,
    ) -> int:
        """AI-Native App 发通知（§7 / P5）：校验 manifest 声明 + category 白名单 →
        `source.kind=app` 经统一 emit() 落权威行 + 卡片承载到 App 服务号会话。

        MVP 边界：recipient 恒为 App 所属 Agent 的主人（不跨主人）。授权 = App 已发布
        manifest 声明了 `notifications.emit`（= 已安装 + 该能力已声明）；category 须在白名单内；
        限频复用 emit() 内的 `_apply_rate_limit`（对 app 源生效，防轰炸）。
        卡片承载受 manifest `card_message` 开关 + 主人偏好双重收敛（delivery_hint 只能在
        主人未显式关闭时打开 card_message，绝不强开主人关掉的渠道）。
        """
        # 延迟导入：registry 依赖 app/hasn（重图），避免模块加载期循环。
        from backend.app.hasn_core.app_platform import ai_native_app_registry

        decl = await ai_native_app_registry.get_emit_declaration(db, app_id)
        if decl is None:
            raise errors.ForbiddenError(msg='app_not_authorized_to_emit')
        categories = decl.get('categories') or []
        if category not in categories:
            raise errors.ForbiddenError(msg='category_not_whitelisted')

        delivery_hint = None
        if want_card and decl.get('card_message'):
            delivery_hint = {'channels': {'card_message': True}}

        source = {
            'kind': 'app',
            'id': app_id,
            'display_name': decl.get('display_name') or app_id,
            'on_behalf_of': owner_hasn_id,
        }
        return await cls.emit(
            db,
            recipient_id=owner_hasn_id,
            source=source,
            category=category,
            type=type,
            title=title,
            body=body,
            payload=payload,
            priority=priority,
            dedupe_key=dedupe_key,
            group_key=group_key,
            delivery_hint=delivery_hint,
        )

    @staticmethod
    async def _is_owner_loopback(
        db: AsyncSession, *, source: dict[str, Any], recipient_id: str
    ) -> bool:
        """判定「自分身→主人」（OwnerLoopback）：source 是 agent 且该分身的主人==recipient。

        优先信任 `source.on_behalf_of`（内部 producer / Agent-JWT 端点设置的可信信号，即该
        分身代表的主人）；缺失才回退 DB 查 HasnAgents.owner_id。非 agent 源、agent→他人、
        agent→agent 均返回 False（不属汇报面）。
        """
        src = source or {}
        if src.get('kind') != 'agent':
            return False
        agent_id = src.get('id')
        if not agent_id:
            return False
        on_behalf = src.get('on_behalf_of')
        if on_behalf:
            return str(on_behalf) == str(recipient_id)
        # 回退：DB 查该分身的主人（延迟导入避免模块加载期循环）
        from backend.app.hasn_core import HasnAgents

        owner = (
            await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == str(agent_id)))
        ).scalar_one_or_none()
        return owner is not None and str(owner) == str(recipient_id)

    @classmethod
    async def _apply_rate_limit(
        cls,
        db: AsyncSession,
        *,
        recipient_id: str,
        source: dict[str, Any],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        """近窗超阈值 → 返回压制了"吵"承载的新 policy；否则原样返回（不可变更新）。"""
        src = source or {}
        src_kind = src.get('kind')
        src_id = src.get('id')
        # 只对带 id 的 app/agent/external 源限频（这些是轰炸向量）；system/user 不限频。
        if src_kind not in ('app', 'agent', 'external') or not src_id:
            return policy
        window_start = _tz.now() - timedelta(seconds=cls.RATE_WINDOW_SECONDS)
        recent = (
            await db.execute(
                select(func.count())
                .select_from(HasnNotifications)
                .where(
                    HasnNotifications.target_id == recipient_id,
                    HasnNotifications.created_time >= window_start,
                    HasnNotifications.source['kind'].astext == src_kind,
                    HasnNotifications.source['id'].astext == src_id,
                )
            )
        ).scalar() or 0
        if recent < cls.RATE_MAX_PER_WINDOW:
            return policy
        channels = dict(policy.get('channels', {}))
        channels['toast'] = False
        channels['push'] = False
        channels['card_message'] = False
        return {**policy, 'channels': channels, 'rate_limited': True}

    @staticmethod
    async def _emit_sync_event(db: AsyncSession, *, row: HasnNotifications) -> None:
        """写 notification.created sync_event，供 daemon 下行同步 → OS Toast（§5）。

        载荷只含 owner 自有数据的非敏感投影（id/category/type/priority/title），与
        message.received sync_event 同口径；明文不外泄（接收方是 owner 自己的节点）。
        """
        from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway

        sync_gw = SqlAlchemySyncGateway()
        await sync_gw._append_sync_event(
            db,
            owner_id=row.target_id,
            hasn_id=row.target_id,
            event_type='notification.created',
            aggregate_type='notification',
            aggregate_id=str(row.id),
            payload={
                'notification_id': row.id,
                'category': row.category,
                'type': row.type,
                'priority': row.priority,
                'title': row.title,
                'source': dict(row.source or {}),
            },
        )

    @staticmethod
    async def _fanout_card(db: AsyncSession, *, row: HasnNotifications, source: dict[str, Any]) -> None:
        """卡片承载 fanout：建/取服务号 → 投递卡片 → 回写 delivery.card_message_id（D1 投影回指）。

        - app/system/external 源 + 收件方=主人 → 服务号（sv_）⇄ 主人 service 会话（直写）；
        - app/system/external 源 + 收件方=Agent（§4.5 发 Agent）→ 服务号 ⇄ Agent service 会话，
          经 route_message 复用 agent dispatch 投 runtime + owner_copy 让主人旁观；
        - agent 源 → 卡片落「主人 ⇄ agent」既有 social 会话（§4.5：agent 本身即会话身份，不建服务号）；
        - user（社交）源 → 默认无卡片承载（不进此分支，category=social 无 card_message）。
        """
        # 延迟导入：carrier 依赖 message_router（重图），避免模块加载期循环。
        from backend.app.notification.service.notification_carrier import (
            deliver_agent_card_to_owner,
            deliver_card_to_agent,
            deliver_card_to_owner,
        )
        from backend.app.notification.service.service_account_service import service_account_service

        src = source or {}
        recipient_is_agent = str(row.target_id).startswith('a_')

        # agent 源 → 「主人 ⇄ agent」social 会话（recipient 恒为主人；agent 自己通知主人）。
        # 收件方本身是 Agent 的 agent→agent 通知出范围（落服务号分支并因 source.kind=agent 不建号而退出）。
        if src.get('kind') == 'agent' and src.get('id') and not recipient_is_agent:
            card_message_id = await deliver_agent_card_to_owner(
                db, recipient_id=row.target_id, source=src, notif=row
            )
            delivery = dict(row.delivery or {})
            delivery['card_message_id'] = card_message_id
            delivery['card_peer'] = str(src.get('id'))
            row.delivery = delivery
            await db.flush()
            return

        # 收件方是 Agent（§4.5 发 Agent）：服务号属该 Agent 的主人；卡片落「服务号 ⇄ Agent」
        # service 会话，经 route_message 复用 agent dispatch 投 runtime + owner_copy 主人旁观。
        if recipient_is_agent:
            from backend.app.hasn_core import HasnAgents

            owner_id = (
                await db.execute(
                    select(HasnAgents.owner_id).where(HasnAgents.hasn_id == str(row.target_id))
                )
            ).scalar_one_or_none()
            if not owner_id:
                return
            account = await service_account_service.get_or_create_for_source(
                db, owner_id=owner_id, source=src
            )
            if account is None:
                # agent/user 源发 Agent 不建服务号、默认无卡片承载（出范围）
                return
            card_message_id = await deliver_card_to_agent(
                db, agent_id=str(row.target_id), account=account, notif=row
            )
            delivery = dict(row.delivery or {})
            delivery['card_message_id'] = card_message_id
            delivery['service_account'] = account.sa_hasn_id
            delivery['card_recipient'] = 'agent'
            row.delivery = delivery
            await db.flush()
            return

        # 收件方是主人（h_）：服务号 ⇄ 主人 service 会话（直写绕权限矩阵，主人自见）。
        account = await service_account_service.get_or_create_for_source(
            db, owner_id=row.target_id, source=src
        )
        if account is None:
            # user 社交源不建服务号、默认无卡片承载
            return
        card_message_id = await deliver_card_to_owner(
            db, recipient_id=row.target_id, account=account, notif=row
        )
        delivery = dict(row.delivery or {})
        delivery['card_message_id'] = card_message_id
        delivery['service_account'] = account.sa_hasn_id
        row.delivery = delivery
        await db.flush()

    # ==================== 读取 / 已读（承袭社区语义，扩展 category） ====================

    @staticmethod
    async def list_notifications(
        db: AsyncSession,
        *,
        recipient_hasn_id: str,
        types: list[str] | None = None,
        categories: list[str] | None = None,
        unread_only: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """通知列表（type/category/unread 过滤 + 游标分页 + 读时聚合）。"""
        stmt = select(HasnNotifications).where(HasnNotifications.target_id == recipient_hasn_id)
        if types:
            stmt = stmt.where(HasnNotifications.type.in_(types))
        if categories:
            stmt = stmt.where(HasnNotifications.category.in_(categories))
        if unread_only:
            stmt = stmt.where(HasnNotifications.read.is_(False))
        if cursor:
            stmt = stmt.where(HasnNotifications.id < int(cursor))
        stmt = stmt.order_by(HasnNotifications.id.desc()).limit(limit + 1)

        rows = (await db.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        rows = rows[:limit]

        # 通知行 → 卡片投影（doc `通知系统统一设计/01` §3.4：cloud 权威投影，前端零拼装）。
        # 延迟导入：carrier 依赖 message_router（重图），避免模块加载期循环。
        from backend.app.notification.service.notification_carrier import project_notification_card

        # 读时聚合：同 group_key 折叠（缺省 group_key 等价 (type,target.id)）
        seen: dict[str, dict[str, Any]] = {}
        items: list[dict[str, Any]] = []
        for n in rows:
            data = n.data or {}
            target = data.get('target', {})
            key = n.group_key or f'{n.type}:{target.get("id", "")}'
            agg_seed = int(data.get('aggregated_count', 1))
            if key in seen and target.get('id') is not None:
                seen[key]['aggregated_count'] += agg_seed
                continue
            entry = {
                'id': n.id,
                'type': n.type,
                'category': n.category,
                'priority': n.priority,
                'source': n.source or {},
                'state': n.state,
                'title': n.title,
                'actor': data.get('actor'),
                'target': target,
                'preview': data.get('preview'),
                'link': data.get('link'),
                'relay_from': data.get('relay_from'),
                'read': n.read,
                'aggregated_count': agg_seed,
                'created_time': n.created_time.isoformat() if n.created_time else None,
                # §3.4 卡片投影：前端折叠进消息列表后直接 CardMessage 渲染；None 则回退扁平字段。
                'card': project_notification_card(n),
            }
            items.append(entry)
            if target.get('id') is not None:
                seen[key] = entry

        return {
            'items': items,
            'next_cursor': str(rows[-1].id) if has_more and rows else None,
            'aggregated': True,
        }

    @staticmethod
    async def unread_count(
        db: AsyncSession,
        *,
        recipient_hasn_id: str,
    ) -> dict[str, Any]:
        """未读总数 + 按 type + 按 category 分组。"""
        base = (
            HasnNotifications.target_id == recipient_hasn_id,
            HasnNotifications.read.is_(False),
        )
        total = (
            await db.execute(select(func.count()).select_from(HasnNotifications).where(*base))
        ).scalar() or 0
        by_type_rows = (
            await db.execute(
                select(HasnNotifications.type, func.count()).where(*base).group_by(HasnNotifications.type)
            )
        ).all()
        by_cat_rows = (
            await db.execute(
                select(HasnNotifications.category, func.count())
                .where(*base)
                .group_by(HasnNotifications.category)
            )
        ).all()
        return {
            'total': int(total),
            'by_type': {row[0]: int(row[1]) for row in by_type_rows},
            'by_category': {row[0]: int(row[1]) for row in by_cat_rows},
        }

    @staticmethod
    async def mark_read(
        db: AsyncSession,
        *,
        recipient_hasn_id: str,
        notification_id: int,
    ) -> None:
        """标记单条已读（仅本人）。read 与 state 双写（§4.1）。"""
        n = (
            await db.execute(
                select(HasnNotifications).where(
                    HasnNotifications.id == notification_id,
                    HasnNotifications.target_id == recipient_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if not n:
            raise errors.NotFoundError(msg='通知不存在')
        n.read = True
        n.state = 'read'
        await db.flush()

    @staticmethod
    async def mark_all_read(
        db: AsyncSession,
        *,
        recipient_hasn_id: str,
        types: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> int:
        """全部已读（可按 type/category 过滤），返回影响条数。"""
        stmt = select(HasnNotifications).where(
            HasnNotifications.target_id == recipient_hasn_id,
            HasnNotifications.read.is_(False),
        )
        if types:
            stmt = stmt.where(HasnNotifications.type.in_(types))
        if categories:
            stmt = stmt.where(HasnNotifications.category.in_(categories))
        rows = (await db.execute(stmt)).scalars().all()
        for n in rows:
            n.read = True
            n.state = 'read'
        await db.flush()
        return len(rows)

    # ==================== 主人偏好 CRUD（§4.4） ====================

    @staticmethod
    async def _get_effective_preference(
        db: AsyncSession, *, owner_id: str, category: str
    ) -> HasnNotificationPreferences | None:
        """取 category 专属偏好，缺失回退 '*' 全局默认，再缺失 None。"""
        rows = (
            await db.execute(
                select(HasnNotificationPreferences).where(
                    HasnNotificationPreferences.owner_id == owner_id,
                    HasnNotificationPreferences.category.in_([category, '*']),
                )
            )
        ).scalars().all()
        specific = next((r for r in rows if r.category == category), None)
        return specific or next((r for r in rows if r.category == '*'), None)

    @staticmethod
    async def list_preferences(db: AsyncSession, *, owner_id: str) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(HasnNotificationPreferences).where(
                    HasnNotificationPreferences.owner_id == owner_id
                )
            )
        ).scalars().all()
        return [
            {
                'category': r.category,
                'channels': r.channels or {},
                'dnd': r.dnd or {},
                'updated_time': r.updated_time.isoformat() if r.updated_time else None,
            }
            for r in rows
        ]

    @classmethod
    async def upsert_preference(
        cls,
        db: AsyncSession,
        *,
        owner_id: str,
        category: str = '*',
        channels: dict[str, Any] | None = None,
        dnd: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """新增/更新一条偏好（按 owner_id+category 唯一）。"""
        existing = (
            await db.execute(
                select(HasnNotificationPreferences).where(
                    HasnNotificationPreferences.owner_id == owner_id,
                    HasnNotificationPreferences.category == category,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = HasnNotificationPreferences(
                owner_id=owner_id,
                category=category,
                channels=dict(channels or {}),
                dnd=dict(dnd or {}),
            )
            db.add(existing)
        else:
            if channels is not None:
                existing.channels = dict(channels)
            if dnd is not None:
                existing.dnd = dict(dnd)
            existing.updated_time = _tz.now()
        await db.flush()
        return {
            'category': existing.category,
            'channels': existing.channels or {},
            'dnd': existing.dnd or {},
        }


notification_service = NotificationService()
