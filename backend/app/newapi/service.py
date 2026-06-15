"""唤星用户 ↔ new-api 映射 + 凭证编排服务（经 NewApiAdminClient HTTP，无 DB 直连）。

2026-06-15 解耦改造（详见 docs/AI网关/实施/2026-06-15-...迁移方案.md §13）：
- 旧实现直连 new-api 第二数据库引擎（newapi_async_db_session + newapi_direct_dao），
  本次全部改为经 `newapi_admin_client`（HTTP 管理 API）落地，**唤星不再连 new-api 库**。
- 仍保留唤星库映射表 `llm_newapi_user_mapping`（CRUD 不变），新增三列：
  `newapi_access_token`（用户 access_token Fernet 密文，复用于建 relay token）、
  `last_synced_used_quota` / `last_synced_at`（§5A 积分账本增量同步游标）。

单位约定：本服务是「积分 = $」业务层与 new-api 原生 `quota`（微单位整数）的换算边界。
quota = 积分 × NEWAPI_QUOTA_PER_DOLLAR（=new-api QuotaPerUnit，协议精度非业务费率）。
"""

import hashlib
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hermes.model import HermesAgentLlmToken
from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.app.newapi.schema.llm_newapi_user_mapping import (
    CreateLlmNewapiUserMappingParam,
    DeleteLlmNewapiUserMappingParam,
    NewApiMappingInfo,
    NewApiQuotaInfo,
    NewApiUsageDetail,
    NewApiUsageDetailItem,
    NewApiUsageSummary,
    NewApiUsageSummaryItem,
    UpdateLlmNewapiUserMappingParam,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.common.pagination import paging_data
from backend.common.security.encryption import key_encryption
from backend.core.conf import settings
from backend.utils.timezone import timezone as _tz


# ========== 单位换算（1 积分 = $1；quota = 积分 × QUOTA_PER_DOLLAR）==========

def credits_to_quota(credits: int) -> int:
    """唤星积分 → new-api quota（整数微单位）。"""
    return int(credits) * settings.NEWAPI_QUOTA_PER_DOLLAR


def quota_to_credits(quota: int) -> float:
    """new-api quota → 唤星积分（= $）。"""
    return quota / settings.NEWAPI_QUOTA_PER_DOLLAR


# 各套餐默认 quota（当 subscription_tier.features 中未配置 newapi_quota 时使用）
DEFAULT_TIER_QUOTA = {
    'free': credits_to_quota(100),        # 微星 100 积分
    'pro': credits_to_quota(1_000),       # 明星 1,000 积分
    'advanced': credits_to_quota(5_000),  # 恒星 5,000 积分
    'flagship': credits_to_quota(20_000), # 超新星 20,000 积分
}

DEFAULT_USERNAME_PREFIX = 'hx_'
AGENT_TOKEN_NAME_PREFIX = 'hermes-agent'
# 用量按模型聚合时对 /log/ 分页的安全上限（非静默截断：到顶会 log.warning）。
SUMMARY_PAGE_SIZE = 100
MAX_SUMMARY_PAGES = 50


# ========== 模块级取数 helpers（service + billing 复用，避免重复 username 解析/聚合）==========

async def resolve_newapi_username(mapping: LlmNewapiUserMapping) -> str:
    """从 new-api 用户对象取 username（稳定来源，兼容历史无 username 列的映射）。

    取不到时退回约定用户名 `hx_{huanxing_user_id}`（与 ensure_user 一致）。
    """
    try:
        user = await newapi_admin_client.get_user(mapping.newapi_user_id)
    except NewApiError as e:
        log.warning(f'[NewApi] 解析 username 失败 newapi_user_id={mapping.newapi_user_id}: {e}')
        user = None
    if user and user.get('username'):
        return str(user['username'])
    return f'{DEFAULT_USERNAME_PREFIX}{mapping.huanxing_user_id}'


async def aggregate_logs_by_model(
    username: str | None,
    start_time: int,
    end_time: int,
    *,
    token_name: str | None = None,
) -> list[dict]:
    """分页拉 /log/ 并按 model_name 聚合（prompt/completion/quota/请求数）。

    /data/ 是 model+hour 桶聚合但 token_used 合并（无 prompt/completion 拆分），
    故需要拆分的概览统计走 /log/ 逐页聚合。到 MAX_SUMMARY_PAGES 顶会告警（非静默截断）。
    """
    by_model: dict[str, dict] = {}
    truncated = False
    for page in range(1, MAX_SUMMARY_PAGES + 1):
        try:
            items, _total = await newapi_admin_client.get_logs(
                username=username,
                token_name=token_name,
                start_timestamp=start_time,
                end_timestamp=end_time,
                page=page,
                page_size=SUMMARY_PAGE_SIZE,
            )
        except NewApiError as e:
            log.warning(f'[NewApi] 读取用量日志失败（username={username} page={page}）: {e}')
            break
        if not items:
            break
        for it in items:
            model = it.get('model_name') or ''
            agg = by_model.setdefault(
                model,
                {'model_name': model, 'prompt_tokens': 0, 'completion_tokens': 0, 'quota': 0, 'request_count': 0},
            )
            agg['prompt_tokens'] += int(it.get('prompt_tokens') or 0)
            agg['completion_tokens'] += int(it.get('completion_tokens') or 0)
            agg['quota'] += int(it.get('quota') or 0)
            agg['request_count'] += 1
        if len(items) < SUMMARY_PAGE_SIZE:
            break
        if page == MAX_SUMMARY_PAGES:
            truncated = True
    if truncated:
        log.warning(
            f'[NewApi] 用量聚合到达分页上限 {MAX_SUMMARY_PAGES} 页'
            f'（username={username} token_name={token_name}），结果可能不完整'
        )
    return sorted(by_model.values(), key=lambda r: r['quota'], reverse=True)


async def summarize_usage(username: str | None, start_time: int, end_time: int) -> dict:
    """分页拉 /log/ 汇总为 UsageSummary 形状（用于 `/api/v1/llm/usage/summary`）。

    username=None → 汇总全部用户（超管口径）。new-api type=2 消费日志只记可计费的成功调用，
    故 success=total、error=0（如实：错误不计费、不在消费日志）。total_cost = 积分（= $）。
    不可达 → 全 0（调用方如实回退，零 fake）。
    """
    requests = prompt = completion = quota = use_time_sum = 0
    truncated = False
    for page in range(1, MAX_SUMMARY_PAGES + 1):
        try:
            items, _total = await newapi_admin_client.get_logs(
                username=username,
                start_timestamp=start_time,
                end_timestamp=end_time,
                page=page,
                page_size=SUMMARY_PAGE_SIZE,
            )
        except NewApiError as e:
            log.warning(f'[NewApi] 用量汇总读取失败（username={username} page={page}）: {e}')
            break
        if not items:
            break
        for it in items:
            requests += 1
            prompt += int(it.get('prompt_tokens') or 0)
            completion += int(it.get('completion_tokens') or 0)
            quota += int(it.get('quota') or 0)
            use_time_sum += int(it.get('use_time') or 0)
        if len(items) < SUMMARY_PAGE_SIZE:
            break
        if page == MAX_SUMMARY_PAGES:
            truncated = True
    if truncated:
        log.warning(f'[NewApi] 用量汇总到达分页上限 {MAX_SUMMARY_PAGES} 页（username={username}），结果可能不完整')
    avg_latency_ms = round((use_time_sum / requests) * 1000) if requests else 0
    return {
        'total_requests': requests,
        'success_requests': requests,
        'error_requests': 0,
        'total_tokens': prompt + completion,
        'total_input_tokens': prompt,
        'total_output_tokens': completion,
        'total_cost': quota_to_credits(quota),  # 积分 = $（1:1）
        'avg_latency_ms': avg_latency_ms,
    }


class LlmNewapiUserMappingService:

    # ========== 基础 CRUD（唤星库）==========

    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LlmNewapiUserMapping:
        llm_newapi_user_mapping = await llm_newapi_user_mapping_dao.get(db, pk)
        if not llm_newapi_user_mapping:
            raise errors.NotFoundError(msg='唤星用户与 new-api 用户映射不存在')
        return llm_newapi_user_mapping

    @staticmethod
    async def get_list(db: AsyncSession, **kwargs) -> dict[str, Any]:
        llm_newapi_user_mapping_select = await llm_newapi_user_mapping_dao.get_select()
        return await paging_data(db, llm_newapi_user_mapping_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LlmNewapiUserMapping]:
        return await llm_newapi_user_mapping_dao.get_all(db)

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLlmNewapiUserMappingParam, **kwargs) -> None:
        await llm_newapi_user_mapping_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLlmNewapiUserMappingParam, **kwargs) -> int:
        return await llm_newapi_user_mapping_dao.update(db, pk, obj)

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLlmNewapiUserMappingParam) -> int:
        return await llm_newapi_user_mapping_dao.delete(db, obj.pks)

    # ========== 凭证解析 helpers ==========

    @staticmethod
    async def _ensure_user_access_token(
        db: AsyncSession, mapping: LlmNewapiUserMapping, username: str
    ) -> str:
        """取该用户已加密缓存的 access_token；缺失/解密失败则现铸并落库复用。

        建/旋转 relay token 必须以用户身份（new-api 无 admin 建 token 接口），
        access_token 即用户身份令牌。缓存避免每次重设密码 + 重新登录。
        """
        if mapping.newapi_access_token:
            try:
                return key_encryption.decrypt(mapping.newapi_access_token)
            except Exception:
                log.warning(f'[NewApi] access_token 解密失败（密钥变更？）重新铸发 user_id={mapping.huanxing_user_id}')
        token = await newapi_admin_client.bootstrap_user_access_token(
            newapi_user_id=mapping.newapi_user_id, username=username
        )
        mapping.newapi_access_token = key_encryption.encrypt(token)
        await db.flush()
        return token

    # ========== new-api 集成业务逻辑（经 HTTP 管理 API）==========

    @staticmethod
    async def ensure_newapi_user(
        db: AsyncSession,
        huanxing_user_id: int,
        *,
        username: str = '',
        nickname: str = '',
        app_code: str = 'huanxing',
        initial_quota: int | None = None,
    ) -> NewApiMappingInfo:
        """确保唤星用户在 new-api 中有对应用户 + 默认 relay token。

        已存在映射 → 直接返回；否则经管理 API：ensure_user（幂等，撞名复用）→ set_user_quota
        → bootstrap access_token（加密落库）→ provision_user_relay_token（取明文 key）→ 写映射。
        """
        existing = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if existing:
            return NewApiMappingInfo(
                huanxing_user_id=existing.huanxing_user_id,
                newapi_user_id=existing.newapi_user_id,
                newapi_token_key=existing.newapi_token_key,
                app_code=existing.app_code,
                status=existing.status,
            )

        quota = initial_quota if initial_quota is not None else DEFAULT_TIER_QUOTA.get('free', credits_to_quota(100))
        newapi_username = username or f'{DEFAULT_USERNAME_PREFIX}{huanxing_user_id}'
        display = nickname or newapi_username

        # 1. 幂等确保 new-api 用户（撞 username → 复用，替代旧 DB ON CONFLICT 自愈）
        newapi_user_id = await newapi_admin_client.ensure_user(
            username=newapi_username, display_name=display
        )
        # 2. 覆盖式设额度（token 无限额度，用户额度由 users.quota 统一控制）
        await newapi_admin_client.set_user_quota(newapi_user_id=newapi_user_id, quota=quota)
        # 3. 铸用户 access_token（建 relay token 必须以用户身份）
        access_token = await newapi_admin_client.bootstrap_user_access_token(
            newapi_user_id=newapi_user_id, username=newapi_username
        )
        # 4. 建默认 relay token + 取明文 key（new-api 自生成 48 字符 alnum，无前缀）
        token_id, token_key = await newapi_admin_client.provision_user_relay_token(
            newapi_user_id=newapi_user_id,
            username=newapi_username,
            user_access_token=access_token,
            name=f'{app_code} 默认 Key',
        )

        # 5. 写映射记录（唤星库），缓存加密 access_token 供后续建 Agent token 复用
        mapping = LlmNewapiUserMapping(
            huanxing_user_id=huanxing_user_id,
            newapi_user_id=newapi_user_id,
            newapi_token_key=token_key,
            newapi_token_id=token_id,
            newapi_access_token=key_encryption.encrypt(access_token),
            app_code=app_code,
            status='active',
            last_synced_used_quota=0,
            last_synced_at=None,
        )
        db.add(mapping)
        await db.flush()

        log.info(
            f'[NewApi] 为唤星用户 {huanxing_user_id} 创建 new-api 用户 {newapi_user_id}，'
            f'token_id={token_id}，quota={quota}'
        )

        return NewApiMappingInfo(
            huanxing_user_id=huanxing_user_id,
            newapi_user_id=newapi_user_id,
            newapi_token_key=token_key,
            app_code=app_code,
            status='active',
        )

    @staticmethod
    async def sync_quota(
        db: AsyncSession,
        huanxing_user_id: int,
        new_quota: int,
        *,
        app_code: str = 'huanxing',
    ) -> None:
        """同步 quota 到 new-api（订阅变更时调用）。"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            log.warning(f'[NewApi] 用户 {huanxing_user_id} 无 new-api 映射，跳过 quota 同步')
            return
        await newapi_admin_client.set_user_quota(newapi_user_id=mapping.newapi_user_id, quota=new_quota)
        log.info(f'[NewApi] 用户 {huanxing_user_id} quota 同步到 {new_quota}')

    @staticmethod
    async def get_quota_info(
        db: AsyncSession,
        huanxing_user_id: int,
        *,
        app_code: str = 'huanxing',
    ) -> NewApiQuotaInfo:
        """查询用户的 new-api 额度信息。"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        try:
            user_quota = await newapi_admin_client.get_user_quota(mapping.newapi_user_id)
        except NewApiError as e:
            log.warning(f'[NewApi] 读取用户额度失败 newapi_user_id={mapping.newapi_user_id}: {e}')
            user_quota = None
        if not user_quota:
            raise errors.NotFoundError(msg='LLM 用户信息不存在')

        try:
            token_remain = await newapi_admin_client.get_token_remain_quota(mapping.newapi_token_id)
        except NewApiError:
            token_remain = None

        return NewApiQuotaInfo(
            total_quota=user_quota['quota'],
            used_quota=user_quota['used_quota'],
            remain_quota=token_remain or 0,
            request_count=user_quota['request_count'],
        )

    @staticmethod
    async def get_usage_summary(
        db: AsyncSession,
        huanxing_user_id: int,
        start_time: int,
        end_time: int,
        *,
        app_code: str = 'huanxing',
    ) -> NewApiUsageSummary:
        """查询用量统计概览（按模型分组）。"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        username = await resolve_newapi_username(mapping)
        rows = await aggregate_logs_by_model(username, start_time, end_time)
        items = [NewApiUsageSummaryItem(**row) for row in rows]

        return NewApiUsageSummary(
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
    ) -> NewApiUsageDetail:
        """查询用量明细（分页）。offset/limit 映射为 /log/ 的 page/page_size。"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')

        username = await resolve_newapi_username(mapping)
        page = (offset // limit) + 1 if limit else 1
        try:
            items, total = await newapi_admin_client.get_logs(
                username=username,
                model_name=model_name,
                start_timestamp=start_time,
                end_timestamp=end_time,
                page=page,
                page_size=limit,
            )
        except NewApiError as e:
            log.warning(f'[NewApi] 读取用量明细失败 username={username}: {e}')
            items, total = [], 0

        records = [
            NewApiUsageDetailItem(
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
            for it in items
        ]
        return NewApiUsageDetail(items=records, total=total)

    @staticmethod
    async def get_api_key(
        db: AsyncSession,
        huanxing_user_id: int,
        *,
        app_code: str = 'huanxing',
    ) -> str:
        """获取用户的 new-api API Key（加 sk- 前缀，new-api 中间件去前缀后取 parts[0]）。"""
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, huanxing_user_id, app_code)
        if not mapping:
            raise errors.NotFoundError(msg='用户未关联 LLM 服务')
        return f'sk-{mapping.newapi_token_key}'

    # ========== Hermes Agent 级 LLM token 隔离（§09）==========

    @staticmethod
    async def ensure_agent_token(
        db: AsyncSession,
        agent_id: str,
        user_id: int,
        *,
        model_allowlist: list[str] | None = None,
        rate_limit_rps: int | None = None,
        per_token_quota: int | None = None,
        name: str = AGENT_TOKEN_NAME_PREFIX,
    ) -> dict:
        """为指定 Agent 签发独立 newapi token（§09 §2.2）。

        幂等：同 agent_id 已有未撤销记录 → 直接返回，不再下发新 token；
        raw_token_key 仅在首次签发时返回，DB 只存 prefix + sha256。
        """
        # 1. 确保父 user 在 newapi 已注册（含 access_token 加密落库）
        mapping_info = await LlmNewapiUserMappingService.ensure_newapi_user(db, user_id)
        newapi_user_id = mapping_info.newapi_user_id

        # 2. 幂等：查现有未撤销记录
        stmt = select(HermesAgentLlmToken).where(
            HermesAgentLlmToken.agent_id == agent_id,
            HermesAgentLlmToken.revoked_at.is_(None),
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return {
                'agent_id': agent_id,
                'newapi_user_id': existing.newapi_user_id,
                'newapi_token_id': existing.newapi_token_id,
                'token_key_prefix': existing.token_key_prefix,
                'raw_token_key': None,
                'reused': True,
            }

        # 3. 取父 user 映射（access_token + username），新签 agent relay token
        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id)
        if not mapping:
            raise errors.ServerError(msg='new-api 映射缺失，无法签发 Agent token')
        username = await resolve_newapi_username(mapping)
        access_token = await LlmNewapiUserMappingService._ensure_user_access_token(db, mapping, username)

        token_name = f'{name}:{agent_id}'
        newapi_token_id, raw_token_key = await newapi_admin_client.provision_user_relay_token(
            newapi_user_id=newapi_user_id,
            username=username,
            user_access_token=access_token,
            name=token_name,
        )

        # 4. 写 hermes_agent_llm_token：只存 prefix + sha256，不存明文
        token_key_prefix = raw_token_key[:8]
        token_key_sha256 = hashlib.sha256(raw_token_key.encode()).hexdigest()

        record = HermesAgentLlmToken(
            agent_id=agent_id,
            user_id=user_id,
            newapi_user_id=newapi_user_id,
            newapi_token_id=newapi_token_id,
            token_key_prefix=token_key_prefix,
            token_key_sha256=token_key_sha256,
            model_allowlist=model_allowlist,
            rate_limit_rps=rate_limit_rps,
            per_token_quota_remaining=per_token_quota,
            runtime_node_id=None,
        )
        db.add(record)
        await db.flush()

        log.info(
            f'[NewApi] Agent {agent_id} token 已签发：'
            f'newapi_token_id={newapi_token_id}, prefix={token_key_prefix}'
        )

        return {
            'agent_id': agent_id,
            'newapi_user_id': newapi_user_id,
            'newapi_token_id': newapi_token_id,
            'token_key_prefix': token_key_prefix,
            'raw_token_key': raw_token_key,
            'reused': False,
        }

    @staticmethod
    async def revoke_agent_token(
        db: AsyncSession,
        agent_id: str,
    ) -> bool:
        """撤销 Agent 当前 token（§09 §2.2）。

        查未撤销记录 → 经管理 API disable_token（status=2）软禁用 → 标 revoked_at=NOW()。
        不存在或已撤销返回 False；成功撤销返回 True。
        """
        stmt = select(HermesAgentLlmToken).where(
            HermesAgentLlmToken.agent_id == agent_id,
            HermesAgentLlmToken.revoked_at.is_(None),
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if not existing:
            return False

        # 1. 经管理 API 软禁用 token（best-effort，失败仅告警，仍标记本地撤销）
        await newapi_admin_client.disable_token(existing.newapi_token_id)

        # 2. 在唤星库标记 revoked_at = NOW()
        update_stmt = (
            update(HermesAgentLlmToken)
            .where(
                HermesAgentLlmToken.agent_id == agent_id,
                HermesAgentLlmToken.revoked_at.is_(None),
            )
            .values(revoked_at=_tz.now())
        )
        await db.execute(update_stmt)
        await db.flush()

        log.info(
            f'[NewApi] Agent {agent_id} token 已撤销：'
            f'newapi_token_id={existing.newapi_token_id}, prefix={existing.token_key_prefix}'
        )
        return True

    @staticmethod
    async def rotate_agent_token(
        db: AsyncSession,
        agent_id: str,
        user_id: int,
    ) -> dict:
        """旋转 Agent token：先撤销旧 token，再签发新 token（§09 §2.2）。"""
        # 1. 撤销旧（如果存在）
        await LlmNewapiUserMappingService.revoke_agent_token(db, agent_id)
        # 2. 签发新（旧记录 revoked_at 已置位，ensure_agent_token 不会复用）
        return await LlmNewapiUserMappingService.ensure_agent_token(
            db, agent_id=agent_id, user_id=user_id,
        )

    @staticmethod
    async def get_usage_summary_by_agent(
        db: AsyncSession,
        agent_id: str,
        start_time: int,
        end_time: int,
    ) -> dict:
        """per-Agent 用量明细（§09 §2.2）。

        按 token_name（hermes-agent:{agent_id}，全局唯一）+ 父 user username 过滤 /log/ 聚合。
        包括已撤销记录（历史用量保留）。
        """
        stmt = select(HermesAgentLlmToken).where(HermesAgentLlmToken.agent_id == agent_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return {'agent_id': agent_id, 'period': [start_time, end_time], 'by_model': []}

        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, record.user_id)
        username = await resolve_newapi_username(mapping) if mapping else None
        token_name = f'{AGENT_TOKEN_NAME_PREFIX}:{agent_id}'
        rows = await aggregate_logs_by_model(
            username, start_time, end_time, token_name=token_name
        )
        return {
            'agent_id': agent_id,
            'period': [start_time, end_time],
            'by_model': rows,
        }

    @staticmethod
    def tier_to_quota(tier_name: str, features: dict | None = None) -> int:
        """将订阅等级转换为 new-api quota。"""
        if features and 'newapi_quota' in features:
            return int(features['newapi_quota'])
        return DEFAULT_TIER_QUOTA.get(tier_name, DEFAULT_TIER_QUOTA['free'])


llm_newapi_user_mapping_service: LlmNewapiUserMappingService = LlmNewapiUserMappingService()
