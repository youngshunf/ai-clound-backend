from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceSkill
from backend.app.marketplace.schema.marketplace_skill import CreateMarketplaceSkillParam, UpdateMarketplaceSkillParam
from backend.app.marketplace.service.resource_id import PUBLIC_VISIBILITY, PUBLISHED_STATUS


class CRUDMarketplaceSkill(CRUDPlus[MarketplaceSkill]):
    @staticmethod
    def _single_skill(result: object) -> MarketplaceSkill | None:
        """将无关联加载的查询结果收紧为单个市场技能。"""
        if result is not None and not isinstance(result, MarketplaceSkill):
            raise TypeError('市场技能单模型查询返回了关联结果')
        return cast(MarketplaceSkill | None, result)

    @staticmethod
    def _skill_sequence(result: Sequence[object]) -> Sequence[MarketplaceSkill]:
        """将无关联加载的查询结果收紧为市场技能序列。"""
        if not all(isinstance(item, MarketplaceSkill) for item in result):
            raise TypeError('市场技能列表查询返回了关联结果')
        return cast(Sequence[MarketplaceSkill], result)

    async def get(self, db: AsyncSession, pk: int) -> MarketplaceSkill | None:
        """
        获取技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :return:
        """
        return self._single_skill(await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取技能市场技能列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceSkill]:
        """
        获取所有技能市场技能

        :param db: 数据库会话
        :return:
        """
        return self._skill_sequence(await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateMarketplaceSkillParam) -> None:
        """
        创建技能市场技能

        :param db: 数据库会话
        :param obj: 创建技能市场技能参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceSkillParam) -> int:
        """
        更新技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :param obj: 更新 技能市场技能参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除技能市场技能

        :param db: 数据库会话
        :param pks: 技能市场技能 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_id(self, db: AsyncSession, skill_id: str) -> MarketplaceSkill | None:
        """
        根据技能ID获取技能

        :param db: 数据库会话
        :param skill_id: 技能ID
        :return:
        """
        return self._single_skill(await self.select_model_by_column(db, skill_id=skill_id))

    async def get_by_namespace_slug(
        self,
        db: AsyncSession,
        namespace: str,
        slug: str,
    ) -> MarketplaceSkill | None:
        """
        通过命名空间和 slug 获取技能

        :param db: 数据库会话
        :param namespace: 命名空间
        :param slug: 技能 slug
        :return:
        """
        stmt = select(MarketplaceSkill).where(
            MarketplaceSkill.namespace == namespace,
            MarketplaceSkill.slug == slug,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_namespace_slug_public(
        self,
        db: AsyncSession,
        namespace: str,
        slug: str,
    ) -> MarketplaceSkill | None:
        stmt = select(MarketplaceSkill).where(
            MarketplaceSkill.namespace == namespace,
            MarketplaceSkill.slug == slug,
            MarketplaceSkill.status == PUBLISHED_STATUS,
            MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_published_public_by_slug(self, db: AsyncSession, slug: str) -> list[MarketplaceSkill]:
        """按裸 slug 列出所有「已发布 + 公开」技能（按下载量降序），用于技能包成员命名归一。

        裸 slug 通常唯一；返回多条表示跨命名空间重名，调用方据此报错让用户用完整
        `namespace/slug` 消歧（D-NAMING：禁止猜测，避免装错技能）。
        """
        stmt = (
            select(MarketplaceSkill)
            .where(
                MarketplaceSkill.slug == slug,
                MarketplaceSkill.namespace.is_not(None),
                MarketplaceSkill.status == PUBLISHED_STATUS,
                MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
            )
            .order_by(MarketplaceSkill.download_count.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_namespace_slug_for_user(
        self,
        db: AsyncSession,
        namespace: str,
        slug: str,
        user_id: int,
    ) -> MarketplaceSkill | None:
        stmt = select(MarketplaceSkill).where(
            MarketplaceSkill.namespace == namespace,
            MarketplaceSkill.slug == slug,
            MarketplaceSkill.user_id == user_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user(self, db: AsyncSession, user_id: int) -> list[MarketplaceSkill]:
        stmt = (
            select(MarketplaceSkill)
            .where(MarketplaceSkill.user_id == user_id)
            .order_by(MarketplaceSkill.id.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def increment_download_count(self, db: AsyncSession, skill_id: str) -> None:
        """
        增加技能下载次数

        :param db: 数据库会话
        :param skill_id: 技能ID
        """
        stmt = (
            update(MarketplaceSkill)
            .where(MarketplaceSkill.skill_id == skill_id)
            .values(download_count=MarketplaceSkill.download_count + 1)
        )
        await db.execute(stmt)

    async def get_select_public(
        self,
        category: str | None = None,
        tags: str | None = None,
        pricing_type: str | None = None,
        is_official: bool | None = None,  # noqa: FBT001
    ) -> Select:
        """
        获取公开技能列表的查询表达式

        :param category: 分类筛选
        :param tags: 标签筛选
        :param pricing_type: 定价类型筛选
        :param is_official: 是否官方筛选
        :return: 查询表达式
        """
        # 构建基础查询 - 只返回公开的技能
        stmt = select(MarketplaceSkill).where(
            MarketplaceSkill.status == PUBLISHED_STATUS,
            MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
        )

        # 添加筛选条件
        if category:
            stmt = stmt.where(MarketplaceSkill.category == category)
        if tags:
            stmt = stmt.where(or_(
                MarketplaceSkill.tags.contains(tags),
                MarketplaceSkill.tags_en.contains(tags),
                MarketplaceSkill.tags_zh.contains(tags),
            ))
        if pricing_type:
            stmt = stmt.where(MarketplaceSkill.pricing_type == pricing_type)
        if is_official is not None:
            stmt = stmt.where(MarketplaceSkill.is_official == is_official)

        # 排序：官方优先，然后按下载量降序
        stmt = stmt.order_by(
            MarketplaceSkill.is_official.desc(),
            MarketplaceSkill.download_count.desc(),
            MarketplaceSkill.id.desc(),
        )

        return stmt

    async def search(
        self,
        db: AsyncSession,
        keyword: str,
        category: str | None = None,
        limit: int = 20,
    ) -> list[MarketplaceSkill]:
        """
        搜索技能

        :param db: 数据库会话
        :param keyword: 搜索关键词
        :param category: 分类筛选
        :param limit: 最大结果数
        :return: 技能列表
        """
        stmt = select(MarketplaceSkill).where(
            MarketplaceSkill.status == PUBLISHED_STATUS,
            MarketplaceSkill.visibility == PUBLIC_VISIBILITY,
            or_(
                MarketplaceSkill.name_zh.ilike(f'%{keyword}%'),
                MarketplaceSkill.name_en.ilike(f'%{keyword}%'),
                MarketplaceSkill.name.ilike(f'%{keyword}%'),
                MarketplaceSkill.description_zh.ilike(f'%{keyword}%'),
                MarketplaceSkill.description_en.ilike(f'%{keyword}%'),
                MarketplaceSkill.skill_id.ilike(f'%{keyword}%'),
                MarketplaceSkill.tags.ilike(f'%{keyword}%'),
                MarketplaceSkill.tags_en.ilike(f'%{keyword}%'),
                MarketplaceSkill.tags_zh.ilike(f'%{keyword}%'),
                MarketplaceSkill.category.ilike(f'%{keyword}%'),
            ),
        )

        if category:
            stmt = stmt.where(MarketplaceSkill.category == category)

        stmt = stmt.order_by(
            MarketplaceSkill.is_official.desc(),
            MarketplaceSkill.download_count.desc(),
        ).limit(limit)

        result = await db.execute(stmt)
        return list(result.scalars().all())


marketplace_skill_dao: CRUDMarketplaceSkill = CRUDMarketplaceSkill(MarketplaceSkill)
