from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Select, and_, or_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnConversations
from backend.app.hasn.schema.hasn_conversations import CreateHasnConversationsParam, UpdateHasnConversationsParam


class CRUDHasnConversations(CRUDPlus[HasnConversations]):
    @staticmethod
    def _single_conversation(result: object) -> HasnConversations | None:
        """将无关联加载的查询结果收紧为单个会话。"""
        if result is not None and not isinstance(result, HasnConversations):
            raise TypeError('会话单模型查询返回了关联结果')
        return cast(HasnConversations | None, result)

    @staticmethod
    def _conversation_sequence(result: Sequence[object]) -> Sequence[HasnConversations]:
        """将无关联加载的查询结果收紧为会话序列。"""
        if not all(isinstance(item, HasnConversations) for item in result):
            raise TypeError('会话列表查询返回了关联结果')
        return cast(Sequence[HasnConversations], result)

    async def get(self, db: AsyncSession, pk: int) -> HasnConversations | None:
        """
        获取HASN 会话

        :param db: 数据库会话
        :param pk: HASN 会话 ID
        :return:
        """
        return self._single_conversation(await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取HASN 会话列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnConversations]:
        """
        获取所有HASN 会话

        :param db: 数据库会话
        :return:
        """
        return self._conversation_sequence(await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateHasnConversationsParam) -> None:
        """
        创建HASN 会话

        :param db: 数据库会话
        :param obj: 创建HASN 会话参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnConversationsParam) -> int:
        """
        更新HASN 会话

        :param db: 数据库会话
        :param pk: HASN 会话 ID
        :param obj: 更新 HASN 会话参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 会话

        :param db: 数据库会话
        :param pks: HASN 会话 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    @staticmethod
    async def allocate_seq(db: AsyncSession, conversation_id: str) -> int | None:
        """原子分配会话内下一序号（R2-02·doc16 §4.1）。

        同事务内 `UPDATE hasn_conversations SET current_seq = current_seq + 1
        WHERE id = :conversation_id RETURNING current_seq`——PG 行锁串行化同会话
        并发发送，返回值即本条消息应写入的 `conversation_seq`（权威顺序事实）。

        禁时间戳 / `MAX(seq)+1` / 客户端序号——唯一取号入口。会话不存在返回
        None（调用方须已在同事务内 get_or_create 会话，故正常不会 None）。
        """
        result = await db.execute(
            update(HasnConversations)
            .where(HasnConversations.id == conversation_id)
            .values(current_seq=HasnConversations.current_seq + 1)
            .returning(HasnConversations.current_seq)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def mark_direct_unreachable(db: AsyncSession, id_a: str, id_b: str) -> int:
        """把 A↔B 的 direct 会话置 unreachable（D4·会话不删但标不可达·修 B5）。

        删除联系人后关系边已断：会话历史**不删**（保留双方消息记录），仅把仍 active 的
        单聊会话状态标为 `unreachable`——语义「关系已解除、需重新加好友才能继续通信」，
        对端后续发消息按无关系门控/暂存。只改 active 行（幂等，已 archived/disbanded 不动）。
        返回标记条数。
        """
        result = cast(
            CursorResult[Any],
            await db.execute(
                update(HasnConversations)
                .where(HasnConversations.type == 'direct')
                .where(HasnConversations.status == 'active')
                .where(
                    or_(
                        and_(
                            HasnConversations.participant_a_id == id_a,
                            HasnConversations.participant_b_id == id_b,
                        ),
                        and_(
                            HasnConversations.participant_a_id == id_b,
                            HasnConversations.participant_b_id == id_a,
                        ),
                    )
                )
                .values(status='unreachable')
            ),
        )
        return result.rowcount or 0


hasn_conversations_dao: CRUDHasnConversations = CRUDHasnConversations(HasnConversations)
