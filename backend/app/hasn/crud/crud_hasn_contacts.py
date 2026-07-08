from collections.abc import Sequence

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnContacts
from backend.app.hasn.schema.hasn_contacts import CreateHasnContactsParam, UpdateHasnContactsParam


class CRUDHasnContacts(CRUDPlus[HasnContacts]):
    async def get(self, db: AsyncSession, pk: int) -> HasnContacts | None:
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnContacts]:
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnContactsParam) -> None:
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnContactsParam) -> int:
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    # ─── 业务方法（从 hasn_core 迁移） ───

    @staticmethod
    async def get_relation(
        db: AsyncSession, owner_id: str, peer_id: str, relation_type: str = 'social',
    ) -> HasnContacts | None:
        """查询单条关系"""
        return (await db.execute(
            select(HasnContacts)
            .where(HasnContacts.owner_id == owner_id)
            .where(HasnContacts.peer_id == peer_id)
            .where(HasnContacts.relation_type == relation_type)
        )).scalars().first()

    @staticmethod
    async def get_bidirectional(
        db: AsyncSession, id_a: str, id_b: str, relation_type: str = 'social',
    ) -> HasnContacts | None:
        """双向查询: A→B 或 B→A"""
        return (await db.execute(
            select(HasnContacts)
            .where(HasnContacts.relation_type == relation_type)
            .where(or_(
                and_(HasnContacts.owner_id == id_a, HasnContacts.peer_id == id_b),
                and_(HasnContacts.owner_id == id_b, HasnContacts.peer_id == id_a),
            ))
        )).scalars().first()

    @staticmethod
    async def list_contacts(
        db: AsyncSession, owner_id: str, relation_type: str | None = None,
        status: str = 'connected', limit: int = 50, offset: int = 0,
    ) -> list[HasnContacts]:
        """获取联系人列表"""
        stmt = select(HasnContacts).where(
            HasnContacts.owner_id == owner_id, HasnContacts.status == status
        )
        if relation_type:
            stmt = stmt.where(HasnContacts.relation_type == relation_type)
        stmt = stmt.order_by(HasnContacts.last_interaction_at.desc().nullslast()).offset(offset).limit(limit)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def get_pending_requests(db: AsyncSession, peer_id: str, limit: int = 20) -> list[HasnContacts]:
        """获取收到的待处理好友请求 (我作为 peer_id 被加方)"""
        return (await db.execute(
            select(HasnContacts)
            .where(or_(
                HasnContacts.peer_id == peer_id,
                HasnContacts.peer_owner_id == peer_id,
            ))
            .where(HasnContacts.status == 'pending')
            .where(HasnContacts.relation_type == 'social')
            .order_by(HasnContacts.created_time.desc())
            .limit(limit)
        )).scalars().all()

    @staticmethod
    async def get_sent_pending_requests(
        db: AsyncSession, owner_id: str, limit: int = 20,
    ) -> list[HasnContacts]:
        """获取自己已发出但还没被对方处理的好友请求 (我作为 owner_id 发起方)"""
        return (await db.execute(
            select(HasnContacts)
            .where(HasnContacts.owner_id == owner_id)
            .where(HasnContacts.status == 'pending')
            .where(HasnContacts.relation_type == 'social')
            .order_by(HasnContacts.created_time.desc())
            .limit(limit)
        )).scalars().all()

    @staticmethod
    async def create_contact(db: AsyncSession, **kwargs) -> HasnContacts:
        """创建联系人关系"""
        obj = HasnContacts(**kwargs)
        db.add(obj)
        await db.flush()
        return obj

    @staticmethod
    async def upsert_connected(
        db: AsyncSession,
        *,
        owner_id: str,
        peer_id: str,
        peer_type: str,
        relation_type: str = 'social',
        trust_level: int = 2,
        peer_owner_id: str | None = None,
        channel_source: str = 'manual',
        add_source: str | None = None,
        request_message: str | None = None,
    ) -> HasnContacts:
        """建立/复活一条 connected 关系边。

        通过唯一约束 uq_hasn_contact_relation(owner_id,peer_id,relation_type) 做 UPSERT：
        - 不存在则插入 connected；
        - 已存在（含历史 archived / 之前被软删的好友）则翻回 connected 并刷新信任等级与连接时间。
        这样 accept 时不会撞唯一约束，且重新加好友能复用历史行（保留 nickname/tags 等本地信息）。
        """
        stmt = (
            pg_insert(HasnContacts)
            .values(
                owner_id=owner_id,
                peer_id=peer_id,
                peer_owner_id=peer_owner_id,
                peer_type=peer_type,
                relation_type=relation_type,
                trust_level=trust_level,
                status='connected',
                channel_source=channel_source,
                add_source=add_source,
                request_message=request_message,
                connected_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=['owner_id', 'peer_id', 'relation_type'],
                set_={
                    'status': 'connected',
                    'trust_level': trust_level,
                    'peer_owner_id': peer_owner_id,
                    'peer_type': peer_type,
                    'add_source': add_source,
                    'request_message': request_message,
                    'connected_at': func.now(),
                    'updated_time': func.now(),
                },
            )
            .returning(HasnContacts)
        )
        # populate_existing：让 RETURNING 的新值覆盖 identity-map 里的旧行
        # （否则复活 archived 行时返回对象仍是陈旧的 status='archived'）
        result = await db.execute(stmt, execution_options={'populate_existing': True})
        await db.flush()
        return result.scalars().first()

    @staticmethod
    async def accept_request(db: AsyncSession, contact_id: int) -> None:
        """接受好友请求"""
        await db.execute(
            update(HasnContacts).where(HasnContacts.id == contact_id)
            .values(status='connected', trust_level=2, connected_at=func.now())
        )

    @staticmethod
    async def reject_request(db: AsyncSession, contact_id: int) -> None:
        """拒绝好友请求"""
        await db.execute(
            update(HasnContacts).where(HasnContacts.id == contact_id)
            .values(status='archived')
        )

    @staticmethod
    async def update_trust_level(db: AsyncSession, contact_id: int, trust_level: int) -> None:
        await db.execute(
            update(HasnContacts).where(HasnContacts.id == contact_id)
            .values(trust_level=trust_level)
        )

    @staticmethod
    async def block(db: AsyncSession, contact_id: int) -> None:
        await db.execute(
            update(HasnContacts).where(HasnContacts.id == contact_id)
            .values(status='blocked', trust_level=0)
        )

    @staticmethod
    async def delete_relation_bidirectional(
        db: AsyncSession, id_a: str, id_b: str, relation_type: str = 'social',
    ) -> int:
        """双向删边（D4·删除联系人·修 B5）：删 A→B 与 B→A 两条同 relation_type 的关系行。

        协议 `hasn.relation.remove` 的云端权威实现基石：单方发起即彻底解除双向关系边
        （人-人好友两条对称行、人-分身单向行都按字面 (owner,peer) 精确命中，
        不会误伤底层的「主人↔主人」好友关系——那两行 peer_id 是主人非分身）。
        返回删除的行数。
        """
        result = await db.execute(
            delete(HasnContacts).where(
                HasnContacts.relation_type == relation_type,
                or_(
                    and_(HasnContacts.owner_id == id_a, HasnContacts.peer_id == id_b),
                    and_(HasnContacts.owner_id == id_b, HasnContacts.peer_id == id_a),
                ),
            )
        )
        return result.rowcount or 0


hasn_contacts_dao: CRUDHasnContacts = CRUDHasnContacts(HasnContacts)
