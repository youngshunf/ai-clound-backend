"""管理端 — new-api 用户 Token/额度/用量 服务（经 HTTP 管理 API，无 DB 直连）

唤星库提供映射（llm_newapi_user_mapping）+ 用户信息（sys_user）+ 订阅等级（user_subscription），
额度/用量经 new-api 管理 API 取数：
- 额度：`GET /api/user/:id`（quota/used_quota/request_count）
- 用量概览：`GET /api/log/` 逐页聚合（按模型拆 prompt/completion）
- 用量明细：`GET /api/log/`（分页）

2026-06-15 解耦：`newapi_async_db_session + newapi_direct_dao` → `newapi_admin_client`。
new-api 不可达时如实降级（跳过该用户额度 / 概览空 / 明细空），绝不伪造（零 fake）。
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.model import User
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
from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.app.newapi.service import aggregate_logs_by_model, resolve_newapi_username
from backend.common.exception import errors
from backend.common.log import log


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

        JOIN sys_user 获取昵称/手机号，LEFT JOIN user_subscription 获取订阅等级，
        然后经 new-api 管理 API 逐个取当前页用户的 quota。
        """
        # 1. 构建唤星库查询
        stmt = (
            select(
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
            )
            .outerjoin(User, LlmNewapiUserMapping.huanxing_user_id == User.id)
            .outerjoin(
                UserSubscription,
                (LlmNewapiUserMapping.huanxing_user_id == UserSubscription.user_id)
                & (LlmNewapiUserMapping.app_code == UserSubscription.app_code),
            )
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

        # 2. 经管理 API 批量取当前页用户 quota（无批量端点 → 逐个 GET，失败跳过该用户）
        newapi_user_ids = [row.newapi_user_id for row in rows]
        quota_map = await newapi_admin_client.get_batch_users_quota(newapi_user_ids)

        # 3. 组装结果
        items = []
        for row in rows:
            quota_info = quota_map.get(row.newapi_user_id, {})
            total_q = quota_info.get('quota', 0)
            used_q = quota_info.get('used_quota', 0)
            items.append(
                AdminNewApiUserOverview(
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
                )
            )

        return AdminNewApiUserList(items=items, total=total)

    @staticmethod
    async def get_user_quota(
        db: AsyncSession,
        huanxing_user_id: int,
        *,
        app_code: str = 'huanxing',
    ) -> AdminQuotaInfo:
        """查询指定用户的详细额度信息（经管理 API）"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        try:
            user_quota = await newapi_admin_client.get_user_quota(mapping.newapi_user_id)
        except NewApiError as exc:
            log.warning(f'[AdminQuota] new-api quota 读取失败 user_id={huanxing_user_id}: {exc!r}')
            raise errors.ServerError(msg='LLM 服务暂时不可用，请稍后重试')
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
        """管理员修改用户额度（经管理 API 覆盖式设置 users.quota）"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        try:
            await newapi_admin_client.set_user_quota(newapi_user_id=mapping.newapi_user_id, quota=new_quota)
        except NewApiError as exc:
            log.warning(f'[AdminQuota] new-api quota 设置失败 user_id={huanxing_user_id}: {exc!r}')
            raise errors.ServerError(msg='LLM 服务暂时不可用，请稍后重试')
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
        """查询指定用户的用量统计（经 /log/ 逐页按模型聚合）"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        username = await resolve_newapi_username(mapping)
        rows = await aggregate_logs_by_model(username, start_time, end_time)
        items = [
            AdminUsageSummaryItem(
                model_name=r['model_name'],
                prompt_tokens=r['prompt_tokens'],
                completion_tokens=r['completion_tokens'],
                quota=r['quota'],
                request_count=r['request_count'],
            )
            for r in rows
        ]

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
        """查询指定用户的用量明细（分页，经 /log/；offset/limit 映射为 page/page_size）"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        username = await resolve_newapi_username(mapping)
        page = (offset // limit) + 1 if limit else 1
        try:
            records, total = await newapi_admin_client.get_logs(
                username=username,
                model_name=model_name,
                start_timestamp=start_time,
                end_timestamp=end_time,
                page=page,
                page_size=limit,
            )
        except NewApiError as exc:
            log.warning(f'[AdminQuota] 读取用量明细失败 username={username}: {exc!r}')
            records, total = [], 0

        items = [
            AdminUsageDetailItem(
                id=int(it.get('id') or 0),
                created_at=int(it.get('created_at') or 0),
                model_name=it.get('model_name'),
                prompt_tokens=int(it.get('prompt_tokens') or 0),
                completion_tokens=int(it.get('completion_tokens') or 0),
                quota=int(it.get('quota') or 0),
                use_time=int(it.get('use_time') or 0),
                is_stream=bool(it.get('is_stream') or False),
                request_id=str(it['request_id']) if it.get('request_id') is not None else None,
                token_name=it.get('token_name'),
            )
            for it in records
        ]
        return AdminUsageDetail(items=items, total=total)


newapi_quota_service: NewApiQuotaService = NewApiQuotaService()
