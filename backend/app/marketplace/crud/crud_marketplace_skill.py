from typing import Sequence, Optional

from sqlalchemy import Select, update, select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceSkill
from backend.app.marketplace.schema.marketplace_skill import CreateMarketplaceSkillParam, UpdateMarketplaceSkillParam


class CRUDMarketplaceSkill(CRUDPlus[MarketplaceSkill]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceSkill | None:
        """
        获取技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取技能市场技能列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceSkill]:
        """
        获取所有技能市场技能

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

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
        return await self.select_model_by_column(db, skill_id=skill_id)

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
        category: Optional[str] = None,
        tags: Optional[str] = None,
        pricing_type: Optional[str] = None,
        is_official: Optional[bool] = None,
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
        stmt = select(MarketplaceSkill).where(MarketplaceSkill.is_private == False)
        
        # 添加筛选条件
        if category:
            stmt = stmt.where(MarketplaceSkill.category == category)
        if tags:
            stmt = stmt.where(MarketplaceSkill.tags.contains(tags))
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
        category: Optional[str] = None,
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
            MarketplaceSkill.is_private == False,
            or_(
                MarketplaceSkill.name.ilike(f'%{keyword}%'),
                MarketplaceSkill.description.ilike(f'%{keyword}%'),
                MarketplaceSkill.tags.ilike(f'%{keyword}%'),
            )
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
