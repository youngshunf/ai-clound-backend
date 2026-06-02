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

        return row.id

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
        """卡片承载 fanout：建/取服务号 → 投递卡片 → 回写 delivery.card_message_id（D1 投影回指）。"""
        # 延迟导入：carrier 依赖 message_router（重图），避免模块加载期循环。
        from backend.app.notification.service.notification_carrier import deliver_card_to_owner
        from backend.app.notification.service.service_account_service import service_account_service

        account = await service_account_service.get_or_create_for_source(
            db, owner_id=row.target_id, source=source
        )
        if account is None:
            # agent/user 源不建服务号（agent 走自有会话/relay；user 社交默认无卡片承载）
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
