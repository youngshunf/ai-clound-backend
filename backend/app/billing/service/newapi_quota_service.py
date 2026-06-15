"""管理端 — new-api 用户 Token/额度/用量 服务

双库查询：
- 唤星库: llm_newapi_user_mapping（映射表）+ sys_user（用户信息）+ user_subscription（订阅等级）
- new-api 库: users（额度）+ logs（用量明细）
"""

from typing import Any

from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.model import User
from backend.app.newapi.crud import llm_newapi_user_mapping_dao, newapi_direct_dao
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.app.billing.model import UserSubscription
from backend.app.billing.schema.newapi_quota import (
    AdminNewApiUserList,
    AdminNewApiUserOverview,
    AdminQuotaInfo,
    AdminUsageDetail,
    AdminUsageDetailItem,
    AdminUsageSummary,
    AdminUsageSummaryItem,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.database.db import newapi_async_db_session


def _mask_token_key(key: str) -> str:
    """API Key 脱敏：sk-hx****xxxx（保留前4后4）"""
    full_key = f'sk-{key}'
    if len(full_key) <= 10:
        return full_key[:4] + '****'
    return full_key[:6] + '****' + full_key[-4:]


class NewApiQuotaService:
    """管理端 new-api 额度/用量管理服务"""

    @staticmethod
    async def get_user_list(
        db: AsyncSession,
        *,
        page: int = 1,
        size: int = 20,
        user_keyword: str | None = None,
        app_code: str | None = None,
        mapping_status: str | None = None,
    ) -> AdminNewApiUserList:
        """分页查询所有用户的 new-api 映射 + 额度信息

        JOIN sys_user 获取昵称/手机号，LEFT JOIN user_subscription 获取订阅等级
        然后批量查 new-api 库获取 quota 信息
        """
        # 1. 构建唤星库查询
        stmt = select(
            LlmNewapiUserMapping.huanxing_user_id,
            LlmNewapiUserMapping.newapi_user_id,
            LlmNewapiUserMapping.newapi_token_key,
            LlmNewapiUserMapping.newapi_token_id,
            LlmNewapiUserMapping.app_code,
            LlmNewapiUserMapping.status.label('mapping_status'),
            User.nickname.label('user_nickname'),
            User.phone.label('user_phone'),
            UserSubscription.tier.label('subscription_tier'),
            UserSubscription.status.label('subscription_status'),
        ).outerjoin(
            User, LlmNewapiUserMapping.huanxing_user_id == User.id
        ).outerjoin(
            UserSubscription,
            (LlmNewapiUserMapping.huanxing_user_id == UserSubscription.user_id)
            & (LlmNewapiUserMapping.app_code == UserSubscription.app_code),
        )

        # 筛选条件
        if user_keyword:
            stmt = stmt.where(
                or_(
                    User.nickname.ilike(f'%{user_keyword}%'),
                    User.phone.ilike(f'%{user_keyword}%'),
                )
            )
        if app_code:
            stmt = stmt.where(LlmNewapiUserMapping.app_code == app_code)
        if mapping_status:
            stmt = stmt.where(LlmNewapiUserMapping.status == mapping_status)

        # 计数
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        # 分页
        stmt = stmt.order_by(LlmNewapiUserMapping.id.desc())
        stmt = stmt.offset((page - 1) * size).limit(size)
        result = await db.execute(stmt)
        rows = result.all()

        if not rows:
            return AdminNewApiUserList(items=[], total=total)

        # 2. 批量查 new-api 库获取 quota
        newapi_user_ids = [row.newapi_user_id for row in rows]
        async with newapi_async_db_session() as newapi_db:
            quota_map = await newapi_direct_dao.get_batch_users_quota(newapi_db, newapi_user_ids)

        # 3. 组装结果
        items = []
        for row in rows:
            quota_info = quota_map.get(row.newapi_user_id, {})
            total_q = quota_info.get('quota', 0)
            used_q = quota_info.get('used_quota', 0)
            items.append(AdminNewApiUserOverview(
                huanxing_user_id=row.huanxing_user_id,
                user_nickname=row.user_nickname,
                user_phone=row.user_phone,
                subscription_tier=row.subscription_tier,
                subscription_status=row.subscription_status,
                newapi_user_id=row.newapi_user_id,
                newapi_token_key_masked=_mask_token_key(row.newapi_token_key),
                newapi_token_key=f'sk-{row.newapi_token_key}',
                newapi_token_id=row.newapi_token_id,
                app_code=row.app_code,
                mapping_status=row.mapping_status,
                total_quota=total_q,
                used_quota=used_q,
                remain_quota=max(total_q - used_q, 0),
                request_count=quota_info.get('request_count', 0),
            ))

        return AdminNewApiUserList(items=items, total=total)

    @staticmethod
    async def get_user_quota(
        db: AsyncSession,
        huanxing_user_id: int,
        *,
        app_code: str = 'huanxing',
    ) -> AdminQuotaInfo:
        """查询指定用户的详细额度信息"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        async with newapi_async_db_session() as newapi_db:
            user_quota = await newapi_direct_dao.get_newapi_user_quota(newapi_db, mapping.newapi_user_id)
            if not user_quota:
                raise errors.NotFoundError(msg='LLM 用户信息不存在')

        return AdminQuotaInfo(
            huanxing_user_id=huanxing_user_id,
            newapi_user_id=mapping.newapi_user_id,
            total_quota=user_quota['quota'],
            used_quota=user_quota['used_quota'],
            remain_quota=max(user_quota['quota'] - user_quota['used_quota'], 0),
            request_count=user_quota['request_count'],
        )

    @staticmethod
    async def update_user_quota(
        db: AsyncSession,
        huanxing_user_id: int,
        new_quota: int,
        *,
        app_code: str = 'huanxing',
    ) -> None:
        """管理员修改用户额度"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        async with newapi_async_db_session.begin() as newapi_db:
            await newapi_direct_dao.update_newapi_quota(
                newapi_db, newapi_user_id=mapping.newapi_user_id, new_quota=new_quota,
            )
        log.info(f'[AdminQuota] 管理员修改用户 {huanxing_user_id} quota 为 {new_quota}')

    @staticmethod
    async def get_usage_summary(
        db: AsyncSession,
        huanxing_user_id: int,
        start_time: int,
        end_time: int,
        *,
        app_code: str = 'huanxing',
    ) -> AdminUsageSummary:
        """查询指定用户的用量统计"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        async with newapi_async_db_session() as newapi_db:
            rows = await newapi_direct_dao.get_usage_summary(
                newapi_db, mapping.newapi_user_id, start_time, end_time,
            )
        items = [AdminUsageSummaryItem(**row) for row in rows]

        return AdminUsageSummary(
            items=items,
            total_prompt_tokens=sum(i.prompt_tokens for i in items),
            total_completion_tokens=sum(i.completion_tokens for i in items),
            total_quota=sum(i.quota for i in items),
            total_requests=sum(i.request_count for i in items),
            period_start=start_time,
            period_end=end_time,
        )

    @staticmethod
    async def get_usage_detail(
        db: AsyncSession,
        huanxing_user_id: int,
        start_time: int,
        end_time: int,
        *,
        model_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        app_code: str = 'huanxing',
    ) -> AdminUsageDetail:
        """查询指定用户的用量明细"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        async with newapi_async_db_session() as newapi_db:
            records, total = await newapi_direct_dao.get_usage_detail(
                newapi_db, mapping.newapi_user_id, start_time, end_time,
                model_name=model_name, limit=limit, offset=offset,
            )
        return AdminUsageDetail(
            items=[AdminUsageDetailItem(**r) for r in records],
            total=total,
        )


newapi_quota_service: NewApiQuotaService = NewApiQuotaService()
