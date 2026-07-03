from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.constants import (
    ERR_SCOPE_LIFECYCLE_INVALID,
    is_lifecycle_transition_valid,
)
from backend.app.hasn.crud.crud_hasn_trade_sessions import hasn_trade_sessions_dao
from backend.app.hasn.model import HasnTradeSessions
from backend.app.hasn.schema.hasn_trade_sessions import (
    CreateHasnTradeSessionsParam,
    DeleteHasnTradeSessionsParam,
    UpdateHasnTradeSessionsParam,
)
from backend.app.hasn.service.hasn_audit_log_service import hasn_audit_log_service
from backend.common.exception import errors
from backend.common.log import log
from backend.common.pagination import paging_data


class HasnTradeSessionsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnTradeSessions:
        """
        获取HASN 交易会话

        :param db: 数据库会话
        :param pk: HASN 交易会话 ID
        :return:
        """
        hasn_trade_sessions = await hasn_trade_sessions_dao.get(db, pk)
        if not hasn_trade_sessions:
            raise errors.NotFoundError(msg='HASN 交易会话不存在')
        return hasn_trade_sessions

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 交易会话列表

        :param db: 数据库会话
        :return:
        """
        hasn_trade_sessions_select = await hasn_trade_sessions_dao.get_select()
        return await paging_data(db, hasn_trade_sessions_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnTradeSessions]:
        """
        获取所有HASN 交易会话

        :param db: 数据库会话
        :return:
        """
        hasn_trade_sessionss = await hasn_trade_sessions_dao.get_all(db)
        return hasn_trade_sessionss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnTradeSessionsParam) -> None:
        """
        创建HASN 交易会话

        :param db: 数据库会话
        :param obj: 创建HASN 交易会话参数
        :return:
        """
        await hasn_trade_sessions_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnTradeSessionsParam) -> int:
        """
        更新HASN 交易会话

        :param db: 数据库会话
        :param pk: HASN 交易会话 ID
        :param obj: 更新HASN 交易会话参数
        :return:
        """
        count = await hasn_trade_sessions_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnTradeSessionsParam) -> int:
        """
        删除HASN 交易会话

        :param db: 数据库会话
        :param obj: HASN 交易会话 ID 列表
        :return:
        """
        count = await hasn_trade_sessions_dao.delete(db, obj.pks)
        return count

    @staticmethod
    async def transition_lifecycle(
        *,
        db: AsyncSession,
        session: HasnTradeSessions,
        new_state: str,
        actor_id: str | None = None,
    ) -> HasnTradeSessions:
        """Scope 生命周期状态转换（Core/02 §7.5.2 状态机）。

        合法转换：pending→active, pending→closed, active→closed, active→expired
        非法转换抛 errors.RequestError(code=ERR_SCOPE_LIFECYCLE_INVALID)。
        转换后写一条 audit log（best-effort）。
        """
        from_state = session.lifecycle_state
        if not is_lifecycle_transition_valid(from_state, new_state):
            raise errors.RequestError(
                code=ERR_SCOPE_LIFECYCLE_INVALID,
                msg=(
                    f'非法的 scope 生命周期转换: {from_state} -> {new_state}'
                ),
            )

        session.lifecycle_state = new_state
        await db.flush()

        # 审计日志（失败不阻断）
        try:
            await hasn_audit_log_service.append(
                db=db,
                actor_id=actor_id or 'system',
                actor_type='system',
                action='scope_lifecycle_transition',
                target_id=str(session.id),
                target_type='trade_session',
                details={
                    'from_state': from_state,
                    'to_state': new_state,
                    'scope': session.scope,
                    'relation_type': session.relation_type,
                },
            )
        except Exception as exc:
            log.warning(f'[trade_session] lifecycle audit append 失败: {exc}')

        return session


hasn_trade_sessions_service: HasnTradeSessionsService = HasnTradeSessionsService()
