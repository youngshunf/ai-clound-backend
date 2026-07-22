"""hasn_im.adapters.sqlalchemy_relation_gateway · RelationGateway 真实现（§9.2·R2-08）

**关系域对外唯一写入口**。联系人 REST、MCP、名片建联、自动首联和后台管理最终都收敛到本 gateway
（R3 切换调用点后，通用 contacts 写 CRUD 关闭）。本类忠实收编现网散落三处的关系写逻辑——
`HasnContactsService`（请求/删除）、`api/v1/app/contacts.py`（accept 建边/信任/拉黑）、
`inbound_release`（首联对称建边+重投）——**判权是搬家不是重写**，行为逐一对齐现网。

**会话自管（与 SyncAppender 不同）**：port 8 方法均不收 `db`——gateway 是高层门面，每次调用
自开一个会话（读用只读视图，写用事务）。现网各写路径今天就是「service/API 各自内部 commit」，
本 gateway 保持同一提交语义。

依赖方向（§0.1）：adapter 层**允许**依赖现网 service/DAO（收编期过渡）；业务模块只认
`hasn_im.ports.RelationGateway` 抽象，不直接 import 本 adapter。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.app.hasn_im.ports.relation_gateway import EffectiveRelation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

# 拉黑派生：现网无独立 blocked 列，统一判定为 status=='blocked' 或 trust_level==0
# （inbound_gatekeeper / permission_engine / trust_gate 三处同口径）。
_BLOCKED_STATUS = 'blocked'
# 解除拉黑复联的默认信任档（普通联系人=2）。现网无独立 unblock，靠 trust-level 端点传 trust≥1
# 把 blocked 翻回 connected；port 的 unblock 无 trust 入参，按基线档复联。
_RECONNECT_TRUST_LEVEL = 2


class RelationGatewayError(Exception):
    """关系写入的业务态/授权前置校验失败（状态非 pending、无权处理等）。"""


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

    # 会话工厂：默认走现网全局 async_db_session（应用级连接池）；测试可注入每测试隔离引擎的
    # sessionmaker（NullPool），避免全局池化连接被 pytest-asyncio 的 per-test 事件循环复用而炸
    # 「Future attached to a different loop」。port 契约仍是「无 db 参数、自管会话」，这只是 adapter
    # 层的可注入测试缝（与 SqlAlchemySyncAppender.gateway / SyncProjector.appender 同款）。
    session_factory: async_sessionmaker | None = None

    def _session(self):
        """开一个会话：注入了工厂用工厂，否则回落全局 async_db_session（惰性 import 避免环依赖）。"""
        if self.session_factory is not None:
            return self.session_factory()
        from backend.database.db import async_db_session

        return async_db_session()

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
        from backend.app.hasn.service.hasn_contacts_service import HasnContactsService

        async with self._session() as db:
            # target 传已解析的云端权威 hasn_id；service 内部会再解析（HASN ID 直接命中 human/agent）。
            return await HasnContactsService.request_contact(
                db,
                requester_hasn_id=from_hasn_id,
                target=to_hasn_id,
                message=message,
                add_source=channel_source or 'other',
            )

    async def accept_request(self, *, request_id: int, decided_by: str) -> dict[str, Any]:
        """通过请求 → 落 hasn_contacts 边 + 回填 resulting_contact_id（忠实复刻 respond accept 三岔）。

        - `auto_first_contact` 首联请求 → 走 `inbound_release.accept_first_contact_request`
          （建 A→发送分身 对称边 + accept + 重投该 peer 全部暂存拦截，内部 commit）；
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

            trust = req.requested_trust_level or 2

            # ① 首联自动请求：对称建边 + 重投暂存（内部 commit）。
            if req.add_source == 'auto_first_contact':
                from backend.app.hasn.service.inbound_release import accept_first_contact_request

                res = await accept_first_contact_request(db, request=req, approver_id=decided_by)
                return {
                    'status': 'connected',
                    'request_id': request_id,
                    'trust_level': res.get('trust_level', trust),
                    'redelivered': res.get('redelivered', 0),
                    'resulting_contact_id': res.get('contact_id'),
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
            await hasn_contact_requests_dao.mark_rejected(db, request_id, decided_by=decided_by)
            await db.commit()
            return {'status': 'rejected', 'request_id': request_id}

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
        from backend.database.db import async_db_session

        async with async_db_session() as db:
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
        from backend.database.db import async_db_session

        async with async_db_session() as db:
            contact = await hasn_contacts_dao.get_relation(db, owner_hasn_id, peer_hasn_id)
            if contact is None:
                raise RelationGatewayError('关系不存在')
            return await HasnContactsService.remove_contact(db, owner_id=owner_hasn_id, contact=contact)

    async def resolve_effective_relation(
        self, *, owner_hasn_id: str, peer_hasn_id: str
    ) -> EffectiveRelation | None:
        """解析有效关系（供通信判权，只读，不反向调用交易/服务 API，§9.1）。

        取 owner→peer 的 social 直连边（通信判权的主边）；`blocked` 按现网口径派生
        （status=='blocked' 或 trust_level==0）。无边返回 None（判权侧按陌生人 fail-closed 处置）。
        """
        from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao
        from backend.database.db import async_db_session

        async with async_db_session() as db:
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
