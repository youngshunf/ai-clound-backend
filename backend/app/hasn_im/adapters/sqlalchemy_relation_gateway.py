"""hasn_im.adapters.sqlalchemy_relation_gateway · RelationGateway 真实现（§9.2·R2-08）

**关系域对外唯一写入口**。联系人 REST、MCP、名片建联、自动首联和后台管理最终都收敛到本 gateway
（R3 切换调用点后，通用 contacts 写 CRUD 关闭）。本类忠实收编现网散落三处的关系写逻辑——
`HasnContactsService`（请求/删除）、`api/v1/app/contacts.py`（accept 建边/信任/拉黑）、
自动首联对称建边与抑制命令重投——**判权是搬家不是重写**，行为逐一对齐现网。

**会话自管（与 SyncAppender 不同）**：port 8 方法均不收 `db`——gateway 是高层门面，每次调用
自开一个会话（读用只读视图，写用事务）。现网各写路径今天就是「service/API 各自内部 commit」，
本 gateway 保持同一提交语义。

依赖方向（§0.1）：adapter 层**允许**依赖现网 service/DAO（收编期过渡）；业务模块只认
`hasn_im.ports.RelationGateway` 抽象，不直接 import 本 adapter。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_im.ports.relation_gateway import EffectiveRelation
from backend.database.schema_names import SCHEMA_NAMES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# 拉黑派生：现网无独立 blocked 列，统一判定为 status=='blocked' 或 trust_level==0
# （inbound_gatekeeper / permission_engine / trust_gate 三处同口径）。
_BLOCKED_STATUS = 'blocked'
# 解除拉黑复联的默认信任档（普通联系人=2）。现网无独立 unblock，靠 trust-level 端点传 trust≥1
# 把 blocked 翻回 connected；port 的 unblock 无 trust 入参，按基线档复联。
_RECONNECT_TRUST_LEVEL = 2


class RelationGatewayError(Exception):
    """关系写入的业务态/授权前置校验失败（状态非 pending、无权处理等）。"""


async def _require_active_identity(db: Any, hasn_id: str) -> tuple[str, str]:
    """从 IM role 可读身份投影校验存在性和存活态，返回 ``(kind, owner_id)``。"""
    from backend.app.hasn_core import HasnAgents, HasnHumans

    if hasn_id.startswith('h_'):
        row = (
            await db.execute(
                sa.select(HasnHumans.status).where(HasnHumans.hasn_id == hasn_id)
            )
        ).first()
        if row is None:
            raise RelationGatewayError(f'身份不存在：{hasn_id}')
        if row.status != 'active':
            raise RelationGatewayError(f'身份已停用：{hasn_id}')
        return 'human', hasn_id
    if hasn_id.startswith('a_'):
        row = (
            await db.execute(
                sa.select(HasnAgents.status, HasnAgents.owner_id).where(
                    HasnAgents.hasn_id == hasn_id
                )
            )
        ).first()
        if row is None:
            raise RelationGatewayError(f'身份不存在：{hasn_id}')
        if row.status != 'active':
            raise RelationGatewayError(f'身份已停用：{hasn_id}')
        return 'agent', row.owner_id
    raise RelationGatewayError(f'身份类型不受支持：{hasn_id}')


def _resolve_status_on_trust_change(current_status: str, trust_level: int) -> str:
    """信任变更时的 status 联动（忠实复刻 contacts.py:_resolve_status_on_trust_change）。

    - trust==0 → 'blocked'（拉黑）；
    - 原 blocked 且 trust≥1 → 'connected'（解除拉黑复联）；
    - 其余不变。
    """
    if trust_level == 0:
        return _BLOCKED_STATUS
    if current_status == _BLOCKED_STATUS and trust_level >= 1:
        return 'connected'
    return current_status


@dataclass(slots=True)
class SqlAlchemyRelationGateway:
    """RelationGateway 的现网实现（收编三处散落关系写，自管会话，与现网同提交语义）。"""

    # 会话工厂：默认走 IM 受限角色连接池；测试可注入每测试隔离引擎的
    # sessionmaker（NullPool），避免全局池化连接被 pytest-asyncio 的 per-test 事件循环复用而炸
    # 「Future attached to a different loop」。port 契约仍是「无 db 参数、自管会话」，这只是 adapter
    # 层的可注入测试缝（与 SqlAlchemySyncAppender.gateway / SyncProjector.appender 同款）。
    session_factory: async_sessionmaker | None = None
    # 消息门控等 IM 内部用例必须让关系写与消息/抑制事实处于同一事务。该绑定会话只由
    # application provider 构造；端口方法仍不向业务调用方暴露 Session。
    bound_session: AsyncSession | None = None

    async def sweep_expired_relation_lifecycle(self) -> dict[str, int]:
        """使用 IM role 批量收敛关系生命周期。"""
        from backend.app.hasn.model import HasnContactRequests, HasnContacts
        from backend.app.hasn.service.hasn_contacts_service import (
            CONTACT_REQUEST_EXPIRE_DAYS,
        )
        from backend.utils.timezone import timezone

        now = timezone.now()
        request_cutoff = now - timedelta(days=CONTACT_REQUEST_EXPIRE_DAYS)
        async with self._session() as db:
            request_result = await db.execute(
                sa.update(HasnContactRequests)
                .where(
                    HasnContactRequests.status == 'pending',
                    HasnContactRequests.created_time < request_cutoff,
                )
                .values(status='expired', decided_at=now)
            )
            contact_result = await db.execute(
                sa.update(HasnContacts)
                .where(
                    HasnContacts.status == 'connected',
                    HasnContacts.auto_expire.isnot(None),
                    HasnContacts.auto_expire < now,
                )
                .values(status='archived')
            )
            await self._finish_write(db)
            return {
                'requests_expired': int(request_result.rowcount or 0),
                'contacts_expired': int(contact_result.rowcount or 0),
            }

    async def ensure_owner_agent_control_edge(
        self,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
    ) -> dict[str, Any]:
        """把身份域已提交的自有分身事实幂等投影为 social+5 控制边。"""
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao

        async with self._session() as db:
            owner_kind, _ = await _require_active_identity(db, owner_hasn_id)
            peer_kind, actual_owner = await _require_active_identity(db, agent_hasn_id)
            if owner_kind != 'human' or peer_kind != 'agent':
                raise RelationGatewayError('控制边只允许主人指向分身')
            if actual_owner != owner_hasn_id:
                raise RelationGatewayError('分身不属于声明的主人')
            row = await hasn_contacts_dao.upsert_connected(
                db,
                owner_id=owner_hasn_id,
                peer_id=agent_hasn_id,
                peer_type='agent',
                relation_type='social',
                trust_level=5,
                peer_owner_id=owner_hasn_id,
                channel_source='system',
                add_source='owner_agent_control',
            )
            await self._finish_write(db)
            return {
                'contact_id': row.id,
                'owner_id': owner_hasn_id,
                'peer_id': agent_hasn_id,
                'trust_level': row.trust_level,
                'status': row.status,
            }

    @asynccontextmanager
    async def _session(self):
        """取得关系事务；绑定形态不关闭调用方持有的会话。"""
        if self.bound_session is not None:
            yield self.bound_session
            return
        if self.session_factory is not None:
            async with self.session_factory() as db:
                yield db
            return
        from backend.database.db import im_service_db_session

        async with im_service_db_session() as db:
            yield db

    async def _finish_write(self, db: Any) -> None:
        """自管形态提交；事务绑定形态只 flush，最终提交由外层用例统一完成。"""
        if self.bound_session is None:
            await db.commit()
        else:
            await db.flush()

    async def materialize_derived_agent(
        self,
        *,
        owner_hasn_id: str,
        peer_agent_hasn_id: str,
        peer_owner_hasn_id: str,
        trust_level: int,
    ) -> dict[str, Any]:
        """把主人关系派生为 owner→对方分身边，供入站门控原事务调用。"""
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao

        async with self._session() as db:
            owner_kind, _ = await _require_active_identity(db, owner_hasn_id)
            peer_kind, actual_peer_owner = await _require_active_identity(
                db,
                peer_agent_hasn_id,
            )
            await _require_active_identity(db, peer_owner_hasn_id)
            if owner_kind != 'human' or peer_kind != 'agent':
                raise RelationGatewayError('自动物化仅支持主人到对方分身')
            if actual_peer_owner != peer_owner_hasn_id:
                raise RelationGatewayError('对方分身主人声明与权威身份不一致')
            row = await hasn_contacts_dao.upsert_connected(
                db,
                owner_id=owner_hasn_id,
                peer_id=peer_agent_hasn_id,
                peer_type='agent',
                relation_type='social',
                trust_level=trust_level,
                peer_owner_id=peer_owner_hasn_id,
                channel_source='system',
                add_source='auto_materialized',
            )
            await self._finish_write(db)
            return {
                'contact_id': row.id,
                'owner_id': owner_hasn_id,
                'peer_id': peer_agent_hasn_id,
                'trust_level': row.trust_level,
                'status': row.status,
            }

    async def ensure_auto_first_contact_request(
        self,
        *,
        from_agent_hasn_id: str,
        receiver_hasn_id: str,
        receiver_owner_hasn_id: str,
        receiver_type: str,
    ) -> int:
        """幂等建立自动首联请求；唯一约束负责并发兜底。"""
        from backend.app.hasn.crud.crud_hasn_contact_requests import (
            hasn_contact_requests_dao,
        )

        async with self._session() as db:
            from_kind, _ = await _require_active_identity(db, from_agent_hasn_id)
            receiver_kind, receiver_owner = await _require_active_identity(
                db,
                receiver_hasn_id,
            )
            await _require_active_identity(db, receiver_owner_hasn_id)
            if from_kind != 'agent':
                raise RelationGatewayError('自动首联发起方必须是分身')
            if receiver_kind != receiver_type:
                raise RelationGatewayError('自动首联接收方类型与权威身份不一致')
            if receiver_owner != receiver_owner_hasn_id:
                raise RelationGatewayError('自动首联审批主人与权威身份不一致')
            pending = await hasn_contact_requests_dao.get_active_pending(
                db,
                from_agent_hasn_id,
                receiver_hasn_id,
                'social',
            )
            if pending is not None:
                return int(pending.id)
            request = await hasn_contact_requests_dao.create_request(
                db,
                from_id=from_agent_hasn_id,
                from_type='agent',
                to_id=receiver_hasn_id,
                to_type=receiver_type,
                to_owner_id=receiver_owner_hasn_id,
                relation_type='social',
                requested_trust_level=2,
                message=None,
                channel_source='system',
                add_source='auto_first_contact',
            )
            await self._finish_write(db)
            return int(request.id)

    async def upsert_release_contact(
        self,
        *,
        owner_hasn_id: str,
        peer_hasn_id: str,
        minimum_trust_level: int = 2,
        request_id: int | None = None,
    ) -> dict[str, Any]:
        """主人放行时幂等建边或提档，现有更高信任等级不回退。"""
        from backend.app.hasn.crud.crud_hasn_contact_requests import (
            hasn_contact_requests_dao,
        )
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao

        if minimum_trust_level < 1 or minimum_trust_level > 5:
            raise RelationGatewayError('放行信任等级必须位于 1..5')
        async with self._session() as db:
            owner_kind, _ = await _require_active_identity(db, owner_hasn_id)
            peer_kind, peer_owner = await _require_active_identity(db, peer_hasn_id)
            if owner_kind != 'human':
                raise RelationGatewayError('只有主人身份可以放行联系人')
            current = await hasn_contacts_dao.get_relation(
                db,
                owner_hasn_id,
                peer_hasn_id,
            )
            trust_level = max(
                minimum_trust_level,
                int(current.trust_level or 0) if current is not None else 0,
            )
            row = await hasn_contacts_dao.upsert_connected(
                db,
                owner_id=owner_hasn_id,
                peer_id=peer_hasn_id,
                peer_type=peer_kind,
                relation_type='social',
                trust_level=trust_level,
                peer_owner_id=peer_owner,
                channel_source='system',
                add_source='inbound_release',
            )
            if request_id is not None:
                request = await hasn_contact_requests_dao.get(db, request_id)
                if request is None:
                    raise RelationGatewayError('关联的自动首联请求不存在')
                if request.status == 'pending':
                    if request.to_owner_id != owner_hasn_id:
                        raise RelationGatewayError('关联请求不属于当前主人')
                    if request.from_id != peer_hasn_id:
                        raise RelationGatewayError('关联请求发起方与放行发送方不一致')
                    await hasn_contact_requests_dao.mark_accepted(
                        db,
                        request_id,
                        decided_by=owner_hasn_id,
                        resulting_contact_id=row.id,
                    )
                elif request.status != 'accepted':
                    raise RelationGatewayError(
                        f'关联请求不可接受 (status={request.status})'
                    )
            await self._finish_write(db)
            return {
                'contact_id': row.id,
                'owner_id': owner_hasn_id,
                'peer_id': peer_hasn_id,
                'trust_level': row.trust_level,
                'status': row.status,
            }

    async def request_contact(
        self,
        *,
        from_hasn_id: str,
        to_hasn_id: str,
        relation_type: str = 'social',
        requested_trust_level: int = 2,
        message: str | None = None,
        channel_source: str | None = None,
    ) -> dict[str, Any]:
        """发起联系人请求 → 委托现网 `HasnContactsService.request_contact`（内部 commit）。

        忠实说明（§9.2 收窄差异，不造假）：现网 `request_contact` 按唤星号/HASN ID 解析目标、
        **强制 social**、trust 由 requester↔目标主人边派生，故本 gateway 的 `relation_type`/
        `requested_trust_level` 入参当前**不被下层采纳**（保留为契约位，待后续切片放宽 service 时贯通）。
        """
        from backend.app.hasn.service.hasn_contacts_service import (
            ContactRequestError,
            HasnContactsService,
        )

        async with self._session() as db:
            await _require_active_identity(db, from_hasn_id)
            # target 传已解析的云端权威 hasn_id；service 内部会再解析（HASN ID 直接命中 human/agent）。
            try:
                return await HasnContactsService.request_contact(
                    db,
                    requester_hasn_id=from_hasn_id,
                    target=to_hasn_id,
                    message=message,
                    add_source=channel_source or 'other',
                )
            except ContactRequestError as exc:
                raise RelationGatewayError(exc.msg) from exc

    async def update_agent_communication_settings(
        self,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
        social_enabled: bool | None = None,
        inbound_policy: str | None = None,
    ) -> dict[str, Any]:
        """主人更新自有分身的 IM 权威通信设置。"""
        allowed_policies = {'auto', 'manual_all', 'manual_strangers'}
        if inbound_policy is not None and inbound_policy not in allowed_policies:
            raise RelationGatewayError('inbound_policy 不受支持')
        if social_enabled is None and inbound_policy is None:
            raise RelationGatewayError('至少提供一个通信设置字段')
        table = SCHEMA_NAMES.im_table('agent_communication_settings')
        async with self._session() as db:
            owner_kind, _ = await _require_active_identity(db, owner_hasn_id)
            agent_kind, actual_owner = await _require_active_identity(db, agent_hasn_id)
            if owner_kind != 'human' or agent_kind != 'agent':
                raise RelationGatewayError('通信设置仅支持主人管理分身')
            if actual_owner != owner_hasn_id:
                raise RelationGatewayError('只能修改自己名下分身的通信设置')
            current = (
                await db.execute(
                    sa.text(
                        f"""
                        SELECT social_enabled, inbound_policy
                        FROM {table}
                        WHERE agent_hasn_id = :agent_hasn_id
                        """  # noqa: S608 表名来自进程固定 schema 配置
                    ),
                    {'agent_hasn_id': agent_hasn_id},
                )
            ).mappings().one_or_none()
            resolved_social = (
                social_enabled
                if social_enabled is not None
                else bool(current['social_enabled']) if current is not None else True
            )
            resolved_policy = (
                inbound_policy
                if inbound_policy is not None
                else str(current['inbound_policy']) if current is not None else 'auto'
            )
            await db.execute(
                sa.text(
                    f"""
                    INSERT INTO {table} (
                        agent_hasn_id, social_enabled, inbound_policy,
                        created_time, updated_time
                    ) VALUES (
                        :agent_hasn_id, :social_enabled, :inbound_policy,
                        now(), now()
                    )
                    ON CONFLICT (agent_hasn_id) DO UPDATE SET
                        social_enabled = EXCLUDED.social_enabled,
                        inbound_policy = EXCLUDED.inbound_policy,
                        updated_time = now()
                    """  # noqa: S608 表名来自进程固定 schema 配置
                ),
                {
                    'agent_hasn_id': agent_hasn_id,
                    'social_enabled': resolved_social,
                    'inbound_policy': resolved_policy,
                },
            )
            await self._finish_write(db)
            return {
                'agent_hasn_id': agent_hasn_id,
                'social_enabled': resolved_social,
                'inbound_policy': resolved_policy,
            }

    async def get_agent_communication_settings(
        self,
        *,
        agent_hasn_id: str,
    ) -> dict[str, Any]:
        """读取权威通信设置；新分身未落显式设置时使用协议默认值。"""
        table = SCHEMA_NAMES.im_table('agent_communication_settings')
        async with self._session() as db:
            kind, _ = await _require_active_identity(db, agent_hasn_id)
            if kind != 'agent':
                raise RelationGatewayError('通信设置只属于分身身份')
            row = (
                await db.execute(
                    sa.text(
                        'SELECT social_enabled, inbound_policy '
                        f'FROM {table} '  # noqa: S608 内部表名由 schema 注册表生成
                        'WHERE agent_hasn_id = :agent_hasn_id'
                    ),
                    {'agent_hasn_id': agent_hasn_id},
                )
            ).mappings().first()
            return {
                'agent_hasn_id': agent_hasn_id,
                'social_enabled': (
                    bool(row['social_enabled']) if row is not None else True
                ),
                'inbound_policy': (
                    str(row['inbound_policy']) if row is not None else 'auto'
                ),
            }

    async def filter_socially_enabled_agents(
        self,
        *,
        agent_hasn_ids: list[str],
    ) -> set[str]:
        """批量过滤公开社交分身；缺失显式设置按默认开启处理。"""
        candidates = {
            agent_hasn_id
            for agent_hasn_id in agent_hasn_ids
            if agent_hasn_id.startswith('a_')
        }
        if not candidates:
            return set()
        table = SCHEMA_NAMES.im_table('agent_communication_settings')
        async with self._session() as db:
            disabled = set(
                (
                    await db.execute(
                        sa.text(
                            'SELECT agent_hasn_id '
                            f'FROM {table} '  # noqa: S608 内部表名由 schema 注册表生成
                            'WHERE agent_hasn_id = ANY(:agent_ids) '
                            'AND social_enabled = false'
                        ),
                        {'agent_ids': sorted(candidates)},
                    )
                ).scalars()
            )
        return candidates - disabled

    async def accept_request(self, *, request_id: int, decided_by: str) -> dict[str, Any]:
        """通过请求 → 落 hasn_contacts 边 + 回填 resulting_contact_id（忠实复刻 respond accept 三岔）。

        - `auto_first_contact` 首联请求 → 建 A→发送分身边 + accept + 重投该 peer 全部抑制命令；
        - agent 目标 → 只建『请求方→分身』单向 agent 边；
        - human 目标 → 互建双向 connected 边。
        授权：仅审批人（`to_owner_id`）可接受。
        """
        from backend.app.hasn.crud.crud_hasn_contact_requests import hasn_contact_requests_dao
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao

        async with self._session() as db:
            req = await hasn_contact_requests_dao.get(db, request_id)
            if req is None:
                raise RelationGatewayError('好友请求不存在')
            if req.status != 'pending':
                raise RelationGatewayError(f'该请求已处理 (status={req.status})')
            if req.to_owner_id != decided_by:
                raise RelationGatewayError('只有被请求方可以接受该请求')
            await _require_active_identity(db, req.from_id)
            await _require_active_identity(db, req.to_id)
            await _require_active_identity(db, decided_by)

            trust = req.requested_trust_level or 2

            # ① 首联自动请求：对称建边 + 重投暂存（内部 commit）。
            if req.add_source == 'auto_first_contact':
                from backend.app.hasn.model import HasnSuppressedMessages
                from backend.app.hasn_im.application.suppression_service import (
                    commit_suppressed_rows,
                )

                existing = await hasn_contacts_dao.get_relation(
                    db,
                    decided_by,
                    req.from_id,
                )
                resolved_trust = max(
                    trust,
                    int(existing.trust_level or 0)
                    if existing is not None
                    else 0,
                )
                contact = await hasn_contacts_dao.upsert_connected(
                    db,
                    owner_id=decided_by,
                    peer_id=req.from_id,
                    peer_type=req.from_type,
                    relation_type='social',
                    trust_level=resolved_trust,
                    peer_owner_id=(
                        (await _require_active_identity(db, req.from_id))[1]
                    ),
                    channel_source='system',
                    add_source='inbound_release',
                )
                await hasn_contact_requests_dao.mark_accepted(
                    db,
                    request_id,
                    decided_by=decided_by,
                    resulting_contact_id=contact.id,
                )
                suppressed_rows = list(
                    (
                        await db.execute(
                            sa.select(HasnSuppressedMessages)
                            .where(
                                HasnSuppressedMessages.owner_id == decided_by,
                                HasnSuppressedMessages.sender_hasn_id
                                == req.from_id,
                                HasnSuppressedMessages.resolved_at.is_(None),
                            )
                            .order_by(HasnSuppressedMessages.id.asc())
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                redelivered = len(
                    await commit_suppressed_rows(db, suppressed_rows)
                )
                await self._finish_write(db)
                return {
                    'status': 'connected',
                    'request_id': request_id,
                    'trust_level': contact.trust_level,
                    'redelivered': redelivered,
                    'resulting_contact_id': contact.id,
                }

            # ② agent 目标：只建『请求方→分身』单向 agent 边。
            if req.to_type == 'agent':
                forward = await hasn_contacts_dao.upsert_connected(
                    db, owner_id=req.from_id, peer_id=req.to_id, peer_type='agent',
                    relation_type=req.relation_type, trust_level=trust,
                    peer_owner_id=req.to_owner_id, channel_source=req.channel_source or 'manual',
                    add_source=req.add_source, request_message=req.message,
                )
                await hasn_contact_requests_dao.mark_accepted(
                    db, request_id, decided_by=decided_by, resulting_contact_id=forward.id,
                )
                await db.commit()
                return {'status': 'connected', 'request_id': request_id,
                        'trust_level': trust, 'resulting_contact_id': forward.id}

            # ③ human 目标：互建双向 connected 边。
            forward = await hasn_contacts_dao.upsert_connected(
                db, owner_id=req.from_id, peer_id=req.to_id, peer_type='human',
                relation_type=req.relation_type, trust_level=trust,
                peer_owner_id=req.to_id, channel_source=req.channel_source or 'manual',
                add_source=req.add_source, request_message=req.message,
            )
            await hasn_contacts_dao.upsert_connected(
                db, owner_id=req.to_id, peer_id=req.from_id, peer_type='human',
                relation_type=req.relation_type, trust_level=trust,
                peer_owner_id=req.from_id, channel_source=req.channel_source or 'manual',
                request_message=req.message,
            )
            await hasn_contact_requests_dao.mark_accepted(
                db, request_id, decided_by=decided_by, resulting_contact_id=forward.id,
            )
            await db.commit()
            return {'status': 'connected', 'request_id': request_id,
                    'trust_level': trust, 'resulting_contact_id': forward.id}

    async def reject_request(self, *, request_id: int, decided_by: str) -> dict[str, Any]:
        """拒绝请求（仅 pending → rejected，不建边）。授权：仅审批人可拒绝。"""
        from backend.app.hasn.crud.crud_hasn_contact_requests import hasn_contact_requests_dao

        async with self._session() as db:
            req = await hasn_contact_requests_dao.get(db, request_id)
            if req is None:
                raise RelationGatewayError('好友请求不存在')
            if req.status != 'pending':
                raise RelationGatewayError(f'该请求已处理 (status={req.status})')
            if req.to_owner_id != decided_by:
                raise RelationGatewayError('只有被请求方可以拒绝该请求')
            await _require_active_identity(db, req.from_id)
            await _require_active_identity(db, req.to_id)
            await _require_active_identity(db, decided_by)
            await hasn_contact_requests_dao.mark_rejected(db, request_id, decided_by=decided_by)
            await db.commit()
            return {'status': 'rejected', 'request_id': request_id}

    async def withdraw_request(self, *, request_id: int, decided_by: str) -> dict[str, Any]:
        """撤回请求（仅发起方可把 pending 请求转为 withdrawn）。"""
        from backend.app.hasn.crud.crud_hasn_contact_requests import hasn_contact_requests_dao

        async with self._session() as db:
            req = await hasn_contact_requests_dao.get(db, request_id)
            if req is None:
                raise RelationGatewayError('好友请求不存在')
            if req.status != 'pending':
                raise RelationGatewayError(f'该请求已处理 (status={req.status})')
            if req.from_id != decided_by:
                raise RelationGatewayError('只有发起方可以撤回该请求')
            await _require_active_identity(db, req.from_id)
            await _require_active_identity(db, req.to_id)
            await hasn_contact_requests_dao.mark_withdrawn(
                db,
                request_id,
                decided_by=decided_by,
            )
            await db.commit()
            return {'status': 'withdrawn', 'request_id': request_id}

    async def update_trust(
        self, *, owner_hasn_id: str, peer_hasn_id: str, trust_level: int
    ) -> dict[str, Any]:
        """调整信任等级（忠实复刻 contacts.py:update_trust_level 的 ORM 改行 + status 联动）。"""
        return await self._apply_trust(owner_hasn_id, peer_hasn_id, trust_level)

    async def block(self, *, owner_hasn_id: str, peer_hasn_id: str) -> dict[str, Any]:
        """拉黑 = 信任降 0 → status 'blocked'（现网无独立端点，靠 trust=0 达成）。"""
        return await self._apply_trust(owner_hasn_id, peer_hasn_id, 0)

    async def unblock(self, *, owner_hasn_id: str, peer_hasn_id: str) -> dict[str, Any]:
        """解除拉黑 = 信任回基线档 → blocked 翻回 connected（现网靠 trust≥1 达成）。"""
        return await self._apply_trust(owner_hasn_id, peer_hasn_id, _RECONNECT_TRUST_LEVEL)

    async def _apply_trust(self, owner_hasn_id: str, peer_hasn_id: str, trust_level: int) -> dict[str, Any]:
        """按 (owner, peer) 定位关系行，改 trust_level + 联动 status（update_trust/block/unblock 共用）。"""
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao

        async with self._session() as db:
            await _require_active_identity(db, owner_hasn_id)
            await _require_active_identity(db, peer_hasn_id)
            row = await hasn_contacts_dao.get_relation(db, owner_hasn_id, peer_hasn_id)
            if row is None:
                raise RelationGatewayError('关系不存在')
            new_status = _resolve_status_on_trust_change(row.status, trust_level)
            row.trust_level = trust_level
            row.status = new_status
            await db.commit()
            return {
                'owner_id': owner_hasn_id,
                'peer_id': peer_hasn_id,
                'trust_level': trust_level,
                'status': new_status,
            }

    async def remove_relation(self, *, owner_hasn_id: str, peer_hasn_id: str) -> dict[str, Any]:
        """删除关系 → 委托现网 `HasnContactsService.remove_contact`（双向删边 + 会话标不可达，内部 commit）。"""
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao
        from backend.app.hasn.service.hasn_contacts_service import HasnContactsService

        async with self._session() as db:
            await _require_active_identity(db, owner_hasn_id)
            contact = await hasn_contacts_dao.get_relation(db, owner_hasn_id, peer_hasn_id)
            if contact is None:
                raise RelationGatewayError('关系不存在')
            return await HasnContactsService.remove_contact(db, owner_id=owner_hasn_id, contact=contact)

    async def update_permissions(
        self,
        *,
        owner_hasn_id: str,
        peer_hasn_id: str,
        permissions: dict[str, str],
    ) -> dict[str, Any]:
        """校验并合并联系人自定义权限覆盖。"""
        from backend.app.hasn.constants import IronLawViolation, validate_against_iron_laws
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao

        async with self._session() as db:
            await _require_active_identity(db, owner_hasn_id)
            await _require_active_identity(db, peer_hasn_id)
            row = await hasn_contacts_dao.get_relation(
                db,
                owner_hasn_id,
                peer_hasn_id,
            )
            if row is None:
                raise RelationGatewayError('关系不存在')
            try:
                validate_against_iron_laws(
                    relation_type=row.relation_type,
                    permissions=permissions,
                    peer_type=row.peer_type,
                    trust_level=row.trust_level,
                )
            except IronLawViolation as exc:
                raise RelationGatewayError(str(exc)) from exc
            merged = dict(row.custom_permissions or {})
            merged.update(permissions)
            row.custom_permissions = merged
            await db.commit()
            return {
                'owner_id': owner_hasn_id,
                'peer_id': peer_hasn_id,
                'custom_permissions': merged,
            }

    async def resolve_effective_relation(
        self, *, owner_hasn_id: str, peer_hasn_id: str
    ) -> EffectiveRelation | None:
        """解析有效关系（供通信判权，只读，不反向调用交易/服务 API，§9.1）。

        取 owner→peer 的 social 直连边（通信判权的主边）；`blocked` 按现网口径派生
        （status=='blocked' 或 trust_level==0）。无边返回 None（判权侧按陌生人 fail-closed 处置）。
        """
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao

        async with self._session() as db:
            try:
                await _require_active_identity(db, owner_hasn_id)
                await _require_active_identity(db, peer_hasn_id)
            except RelationGatewayError:
                return None
            row = await hasn_contacts_dao.get_relation(db, owner_hasn_id, peer_hasn_id)
        if row is None:
            return None
        return EffectiveRelation(
            relation_type=row.relation_type,
            trust_level=row.trust_level,
            status=row.status,
            blocked=(row.status == _BLOCKED_STATUS or row.trust_level == 0),
            scope=row.scope,
            custom_permissions=row.custom_permissions or {},
        )
