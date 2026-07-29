import hashlib
import json

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_playbook import playbook_dao
from backend.app.hasn_growth.model import (
    GrowthProject,
    GrowthProjectPlaybook,
    Playbook,
    PlaybookVersion,
)
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


def _definition(playbook: Playbook) -> dict[str, Any]:
    """提取会影响执行的打法定义，排除可变归属和展示状态。"""
    return {
        'name': playbook.name,
        'goal': playbook.goal,
        'target_profile': playbook.target_profile,
        'cadence': playbook.cadence,
        'tone_guide': playbook.tone_guide,
        'exit_rule': playbook.exit_rule,
    }


def _frozen_definition(version: PlaybookVersion) -> dict[str, Any]:
    """从不可变版本读取与当前打法相同口径的执行定义。"""
    return {
        'name': version.name,
        'goal': version.goal,
        'target_profile': version.target_profile,
        'cadence': version.cadence,
        'tone_guide': version.tone_guide,
        'exit_rule': version.exit_rule,
    }


def _definition_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _repair_migration_definition_hash(
    frozen: PlaybookVersion,
    definition: dict[str, Any],
    definition_hash: str,
) -> bool:
    """仅在迁移快照字段完全一致时修正旧算法哈希。"""
    if frozen.created_by_kind != 'migration' or _frozen_definition(frozen) != definition:
        return False
    frozen.definition_hash = definition_hash
    return True


def _adoption_to_dict(
    adoption: GrowthProjectPlaybook,
    version: PlaybookVersion,
) -> dict[str, Any]:
    return {
        'id': adoption.id,
        'growth_project_id': str(adoption.growth_project_id),
        'playbook_id': adoption.playbook_id,
        'playbook_version': adoption.playbook_version,
        'status': adoption.status,
        'configuration': adoption.configuration_snapshot,
        'definition': {
            'name': version.name,
            'goal': version.goal,
            'target_profile': version.target_profile,
            'cadence': version.cadence,
            'tone_guide': version.tone_guide,
            'exit_rule': version.exit_rule,
        },
        'definition_hash': version.definition_hash,
    }


class PlaybookService:
    @staticmethod
    async def _owned_growth(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        for_update: bool,
    ) -> GrowthProject:
        try:
            project_id = growth_project_id if isinstance(growth_project_id, UUID) else UUID(str(growth_project_id))
        except (TypeError, ValueError) as exc:
            raise errors.NotFoundError(msg='获客项目不存在') from exc
        statement = sa.select(GrowthProject).where(
            GrowthProject.id == project_id,
            GrowthProject.owner_hasn_id == owner_hasn_id,
        )
        if for_update:
            statement = statement.with_for_update()
        growth = (await db.execute(statement)).scalar_one_or_none()
        if growth is None:
            raise errors.NotFoundError(msg='获客项目不存在')
        return growth

    @staticmethod
    def _visibility(
        *,
        user_id: int,
        enterprise_id: int | None,
    ) -> Any:
        clauses = [
            sa.and_(
                Playbook.is_builtin.is_(True),
                Playbook.owner_scope != 'enterprise',
            ),
            sa.and_(
                Playbook.owner_scope == 'personal',
                Playbook.user_id == user_id,
            ),
        ]
        if enterprise_id is not None:
            clauses.append(
                sa.and_(
                    Playbook.owner_scope == 'enterprise',
                    Playbook.enterprise_id == enterprise_id,
                )
            )
        return sa.or_(*clauses)

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
            visibility.append(sa.and_(Playbook.owner_scope == 'enterprise', Playbook.enterprise_id == enterprise_id))
        rows = (
            (
                await db.execute(
                    sa
                    .select(Playbook)
                    .where(sa.or_(*visibility))
                    .order_by(Playbook.is_builtin.desc(), Playbook.name.asc(), Playbook.id.asc())
                )
            )
            .scalars()
            .all()
        )
        return [_playbook_to_dict(p) for p in rows]

    async def recommend_for_project(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        user_id: int,
        growth_project_id: str | UUID,
    ) -> list[dict[str, Any]]:
        """只读返回可见打法候选；系统推荐不得隐式创建项目采用关系。"""
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        playbooks = (
            (
                await db.execute(
                    sa
                    .select(Playbook)
                    .where(
                        Playbook.enabled.is_(True),
                        self._visibility(
                            user_id=user_id,
                            enterprise_id=growth.enterprise_id,
                        ),
                    )
                    .order_by(
                        Playbook.is_builtin.desc(),
                        Playbook.name,
                        Playbook.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        active_rows = (
            (
                await db.execute(
                    sa.select(GrowthProjectPlaybook).where(
                        GrowthProjectPlaybook.growth_project_id == growth.id,
                        GrowthProjectPlaybook.status == 'active',
                    )
                )
            )
            .scalars()
            .all()
        )
        active_by_playbook = {
            row.playbook_id: {
                'growth_project_playbook_id': row.id,
                'playbook_version': row.playbook_version,
            }
            for row in active_rows
        }
        return [
            {
                **_playbook_to_dict(playbook),
                'playbook_id': playbook.id,
                'version': playbook.version,
                'recommendation_kind': 'available',
                'adopted': active_by_playbook.get(playbook.id),
            }
            for playbook in playbooks
        ]

    async def adopt_for_project(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        user_id: int,
        growth_project_id: str | UUID,
        playbook_id: int,
        expected_playbook_version: int,
        configuration: dict[str, Any],
    ) -> dict[str, Any]:
        """主人显式采用当前打法版本，并固化定义与项目配置快照。"""
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        if growth.status == 'archived':
            raise errors.ConflictError(
                msg='获客项目已归档，不能采用打法',
                data={'error_code': 'GROWTH_PROJECT_ARCHIVED'},
            )
        playbook = (
            await db.execute(
                sa
                .select(Playbook)
                .where(
                    Playbook.id == playbook_id,
                    Playbook.enabled.is_(True),
                    self._visibility(
                        user_id=user_id,
                        enterprise_id=growth.enterprise_id,
                    ),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if playbook is None:
            raise errors.NotFoundError(msg='可用打法不存在')
        if playbook.version != expected_playbook_version:
            raise errors.ConflictError(
                msg='打法版本已变化，请刷新后重试',
                data={
                    'error_code': 'PLAYBOOK_VERSION_CONFLICT',
                    'current_version': playbook.version,
                },
            )
        definition = _definition(playbook)
        definition_hash = _definition_hash(definition)
        frozen = (
            await db.execute(
                sa.select(PlaybookVersion).where(
                    PlaybookVersion.playbook_id == playbook.id,
                    PlaybookVersion.version == playbook.version,
                )
            )
        ).scalar_one_or_none()
        if frozen is None:
            frozen = PlaybookVersion(
                playbook_id=playbook.id,
                version=playbook.version,
                name=playbook.name,
                goal=playbook.goal,
                target_profile=playbook.target_profile,
                cadence=playbook.cadence,
                tone_guide=playbook.tone_guide,
                exit_rule=playbook.exit_rule,
                definition_hash=definition_hash,
                created_by_kind='owner',
                created_by_id=str(user_id),
            )
            db.add(frozen)
            await db.flush()
        elif frozen.definition_hash != definition_hash and not _repair_migration_definition_hash(
            frozen,
            definition,
            definition_hash,
        ):
            # 首次引入版本表的 SQL 迁移使用 concat_ws 计算哈希，与运行时规范 JSON 算法不同。
            # 仅当迁移快照六个定义字段逐项一致时修正哈希元数据；字段不一致仍按未升版本拒绝。
            raise errors.ConflictError(
                msg='打法当前版本的定义已被修改但未递增版本号',
                data={'error_code': 'PLAYBOOK_VERSION_HASH_CONFLICT'},
            )
        existing = (
            await db.execute(
                sa.select(GrowthProjectPlaybook).where(
                    GrowthProjectPlaybook.growth_project_id == growth.id,
                    GrowthProjectPlaybook.playbook_id == playbook.id,
                    GrowthProjectPlaybook.playbook_version == playbook.version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.configuration_snapshot != configuration:
                raise errors.ConflictError(
                    msg='该打法版本已用其他项目配置采用',
                    data={'error_code': 'PLAYBOOK_CONFIGURATION_CONFLICT'},
                )
            if existing.status != 'active':
                existing.status = 'active'
                await db.flush()
            return _adoption_to_dict(existing, frozen)
        prior_rows = (
            (
                await db.execute(
                    sa.select(GrowthProjectPlaybook).where(
                        GrowthProjectPlaybook.growth_project_id == growth.id,
                        GrowthProjectPlaybook.playbook_id == playbook.id,
                        GrowthProjectPlaybook.status == 'active',
                    )
                )
            )
            .scalars()
            .all()
        )
        for prior in prior_rows:
            prior.status = 'retired'
        adoption = GrowthProjectPlaybook(
            growth_project_id=growth.id,
            playbook_id=playbook.id,
            playbook_version=playbook.version,
            status='active',
            configuration_snapshot=configuration,
        )
        db.add(adoption)
        await db.flush()
        return _adoption_to_dict(adoption, frozen)

    async def get_execution_snapshot(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        growth_project_playbook_id: int,
        require_active: bool = True,
    ) -> dict[str, Any]:
        """解析执行时冻结定义；历史读取可显式允许 retired 关系。"""
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        statement = (
            sa
            .select(GrowthProjectPlaybook, PlaybookVersion)
            .join(
                PlaybookVersion,
                sa.and_(
                    PlaybookVersion.playbook_id == GrowthProjectPlaybook.playbook_id,
                    PlaybookVersion.version == GrowthProjectPlaybook.playbook_version,
                ),
            )
            .where(
                GrowthProjectPlaybook.id == growth_project_playbook_id,
                GrowthProjectPlaybook.growth_project_id == growth.id,
            )
        )
        if require_active:
            statement = statement.where(
                GrowthProjectPlaybook.status == 'active',
            )
        row = (await db.execute(statement)).one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='项目打法版本不存在')
        return _adoption_to_dict(row[0], row[1])

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
