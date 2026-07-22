from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceTemplate
from backend.app.marketplace.schema.marketplace_template import (
    CreateMarketplaceTemplateParam,
    UpdateMarketplaceTemplateParam,
)
from backend.app.marketplace.service.resource_id import PUBLIC_VISIBILITY, PUBLISHED_STATUS


class CRUDMarketplaceTemplate(CRUDPlus[MarketplaceTemplate]):
    @staticmethod
    def _single_template(result: object) -> MarketplaceTemplate | None:
        """将不带关联加载的查询结果收紧为单个市场模板。"""
        if result is not None and not isinstance(result, MarketplaceTemplate):
            raise TypeError('市场模板单模型查询返回了关联结果')
        return cast(MarketplaceTemplate | None, result)

    @staticmethod
    def _template_sequence(result: object) -> Sequence[MarketplaceTemplate]:
        """将不带关联加载的查询结果收紧为市场模板序列。"""
        if not isinstance(result, Sequence) or not all(isinstance(item, MarketplaceTemplate) for item in result):
            raise TypeError('市场模板列表查询返回了关联结果')
        return cast(Sequence[MarketplaceTemplate], result)

    async def get(self, db: AsyncSession, pk: int) -> MarketplaceTemplate | None:
        """
        获取技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :param pk: 技能市场模板表（Agent模板/技能包/SOP包） ID
        :return:
        """
        return self._single_template(await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取技能市场模板表（Agent模板/技能包/SOP包）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceTemplate]:
        """
        获取所有技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :return:
        """
        return self._template_sequence(await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateMarketplaceTemplateParam) -> None:
        """
        创建技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :param obj: 创建技能市场模板表（Agent模板/技能包/SOP包）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceTemplateParam) -> int:
        """
        更新技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :param pk: 技能市场模板表（Agent模板/技能包/SOP包） ID
        :param obj: 更新 技能市场模板表（Agent模板/技能包/SOP包）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :param pks: 技能市场模板表（Agent模板/技能包/SOP包） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_id(self, db: AsyncSession, template_id: str) -> MarketplaceTemplate | None:
        """
        根据模板ID获取模板

        :param db: 数据库会话
        :param template_id: 模板ID
        :return:
        """
        return self._single_template(await self.select_model_by_column(db, template_id=template_id))

    async def get_by_namespace_slug(
        self,
        db: AsyncSession,
        namespace: str,
        slug: str,
    ) -> MarketplaceTemplate | None:
        stmt = select(MarketplaceTemplate).where(
            MarketplaceTemplate.namespace == namespace,
            MarketplaceTemplate.slug == slug,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_namespace_slug_public(
        self,
        db: AsyncSession,
        namespace: str,
        slug: str,
    ) -> MarketplaceTemplate | None:
        stmt = select(MarketplaceTemplate).where(
            MarketplaceTemplate.namespace == namespace,
            MarketplaceTemplate.slug == slug,
            MarketplaceTemplate.status == PUBLISHED_STATUS,
            MarketplaceTemplate.visibility == PUBLIC_VISIBILITY,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_namespace_slug_for_user(
        self,
        db: AsyncSession,
        namespace: str,
        slug: str,
        user_id: int,
    ) -> MarketplaceTemplate | None:
        stmt = select(MarketplaceTemplate).where(
            MarketplaceTemplate.namespace == namespace,
            MarketplaceTemplate.slug == slug,
            MarketplaceTemplate.user_id == user_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user(self, db: AsyncSession, user_id: int) -> list[MarketplaceTemplate]:
        stmt = (
            select(MarketplaceTemplate)
            .where(MarketplaceTemplate.user_id == user_id)
            .order_by(MarketplaceTemplate.id.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_select_public(
        self,
        category: str | None = None,
        tags: str | None = None,
        pricing_type: str | None = None,
        is_official: bool | None = None,  # noqa: FBT001
    ) -> Select:
        """
        获取公开模板列表的查询表达式

        :param category: 分类筛选
        :param tags: 标签筛选
        :param pricing_type: 定价类型筛选
        :param is_official: 是否官方筛选
        :return: 查询表达式
        """
        # 构建基础查询 - 只返回公开的模板
        stmt = select(MarketplaceTemplate).where(
            MarketplaceTemplate.status == PUBLISHED_STATUS,
            MarketplaceTemplate.visibility == PUBLIC_VISIBILITY,
        )

        # 添加筛选条件
        if category:
            stmt = stmt.where(MarketplaceTemplate.category == category)
        if tags:
            stmt = stmt.where(MarketplaceTemplate.tags.contains(tags))
        if pricing_type:
            stmt = stmt.where(MarketplaceTemplate.pricing_type == pricing_type)
        if is_official is not None:
            stmt = stmt.where(MarketplaceTemplate.is_official == is_official)

        # 排序：官方优先，然后按下载量降序
        stmt = stmt.order_by(
            MarketplaceTemplate.is_official.desc(),
            MarketplaceTemplate.download_count.desc(),
            MarketplaceTemplate.id.desc(),
        )

        return stmt

    async def increment_download_count(self, db: AsyncSession, template_id: str) -> None:
        """
        增加模板下载次数

        :param db: 数据库会话
        :param template_id: 模板ID
        """
        stmt = (
            update(MarketplaceTemplate)
            .where(MarketplaceTemplate.template_id == template_id)
            .values(download_count=MarketplaceTemplate.download_count + 1)
        )
        await db.execute(stmt)


marketplace_template_dao: CRUDMarketplaceTemplate = CRUDMarketplaceTemplate(MarketplaceTemplate)
