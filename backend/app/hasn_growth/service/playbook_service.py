from typing import Any, Sequence

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_playbook import playbook_dao
from backend.app.hasn_growth.model import Playbook
from backend.app.hasn_growth.schema.playbook import CreatePlaybookParam, DeletePlaybookParam, UpdatePlaybookParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


def _playbook_to_dict(p: Playbook) -> dict[str, Any]:
    """owner 视图序列化（打法管理页只读展示：目标/节奏/语气/止损）。"""
    return {
        'id': p.id,
        'name': p.name,
        'enabled': p.enabled,
        'goal': p.goal,
        'target_profile': p.target_profile,
        'cadence': p.cadence,
        'tone_guide': p.tone_guide,
        'exit_rule': p.exit_rule,
        'is_builtin': p.is_builtin,
        'user_id': p.user_id,
        'owner_scope': p.owner_scope,
        'enterprise_id': p.enterprise_id,
    }


class PlaybookService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Playbook:
        """
        获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param pk: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID
        :return:
        """
        playbook = await playbook_dao.get(db, pk)
        if not playbook:
            raise errors.NotFoundError(msg='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义不存在')
        return playbook

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义列表

        :param db: 数据库会话
        :return:
        """
        playbook_select = await playbook_dao.get_select()
        return await paging_data(db, playbook_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Playbook]:
        """
        获取所有获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :return:
        """
        playbook_list = await playbook_dao.get_all(db)
        return playbook_list

    @staticmethod
    async def list_for_owner(
        db: AsyncSession, *, user_id: int, enterprise_id: int | None = None
    ) -> list[dict[str, Any]]:
        """owner 可见打法列表（内置 ∪ 本人自定义 ∪ 企业 playbook），打法管理页只读展示。

        - 内置（``is_builtin=true`` 或 ``user_id IS NULL`` 且 owner_scope!='enterprise'）对所有 owner 可见；
        - 自定义（owner_scope='personal'）仅本人；
        - 企业 playbook（owner_scope='enterprise'）：仅当前企业上下文成员可见（GE3 自播种产物）。
        内置排前，再按名称稳定排序。
        """
        visibility = [
            sa.and_(Playbook.is_builtin.is_(True), Playbook.owner_scope != 'enterprise'),
            sa.and_(Playbook.owner_scope == 'personal', Playbook.user_id == user_id),
        ]
        if enterprise_id is not None:
            visibility.append(
                sa.and_(Playbook.owner_scope == 'enterprise', Playbook.enterprise_id == enterprise_id)
            )
        rows = (
            await db.execute(
                sa.select(Playbook)
                .where(sa.or_(*visibility))
                .order_by(Playbook.is_builtin.desc(), Playbook.name.asc(), Playbook.id.asc())
            )
        ).scalars().all()
        return [_playbook_to_dict(p) for p in rows]

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreatePlaybookParam) -> None:
        """
        创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param obj: 创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数
        :return:
        """
        await playbook_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdatePlaybookParam) -> int:
        """
        更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param pk: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID
        :param obj: 更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数
        :return:
        """
        count = await playbook_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeletePlaybookParam) -> int:
        """
        删除获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param obj: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID 列表
        :return:
        """
        count = await playbook_dao.delete(db, obj.pks)
        return count


playbook_service: PlaybookService = PlaybookService()
