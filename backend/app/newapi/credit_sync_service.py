"""积分账本闭环 — new-api quota ↔ user_credit_balance 1:1 镜像（§5A，2026-06-15 福仔确认）。

积分由唤星系统记账（`user_credit_balance` 为记账权威：充值/订阅/赠送/过期分桶/FIFO 扣减/审计）。
new-api quota 与积分 **1:1 镜像**（1 积分 = $1；quota = 积分 × NEWAPI_QUOTA_PER_DOLLAR，仅 new-api 边界编码）。
消费在 new-api 扣 `used_quota`，本服务把消费**每小时增量回扣**账本，并把 new-api 可用额度重设为账本剩余。

两个公共入口：
- `sync_quota_to_balance(db, user_id)`：**入账后**（充值/订阅/赠送）令 new-api 可用 = 账本剩余×RATE（§5A.4，补缺口 2）。
- `reconcile_user(db, user_id)` / `reconcile_all(db)`：**每小时**增量回扣 + 重设 quota + 推进游标（§5A.5，补缺口 1）。

降级（new-api 不可达 / 无映射 / 无 new-api 用户）→ 跳过该用户并如实记日志，**绝不臆造扣减**（零 fake）。

@author Ysf
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.common.log import log
from backend.core.conf import settings
from backend.utils.timezone import timezone


def _rate() -> int:
    """new-api 边界编码精度（= QuotaPerUnit = $1 对应的 quota 微单位）。"""
    return settings.NEWAPI_QUOTA_PER_DOLLAR or 1


def _target_quota_for_remaining(used_quota: int, remaining_credits: Decimal) -> int:
    """目标 quota = used_quota + 账本剩余积分 × RATE（令 new-api 可用 = 剩余积分）。"""
    return int(used_quota) + int(remaining_credits * _rate())


class CreditSyncService:
    """§5A 积分账本闭环：入账推额度 + 每小时增量回扣。"""

    @staticmethod
    async def sync_quota_to_balance(
        db: AsyncSession,
        user_id: int,
        *,
        app_code: str = 'huanxing',
    ) -> bool:
        """§5A.4/D6：入账后令 new-api 可用额度 = 账本剩余积分 × RATE。

        充值/订阅/赠送在 `add_credits` 之后调用。无映射 / new-api 不可达 → 返回 False（如实降级）。
        """
        from backend.app.billing.service.credit_service import credit_service

        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping:
            log.info(f'[CreditSync] user_id={user_id} 无 new-api 映射，跳过 quota 同步')
            return False
        try:
            quota = await newapi_admin_client.get_user_quota(mapping.newapi_user_id)
        except NewApiError as exc:
            log.warning(f'[CreditSync] sync_quota_to_balance 读 quota 失败 user_id={user_id}: {exc!r}')
            return False
        if not quota:
            log.warning(f'[CreditSync] new-api 无此用户 newapi_user_id={mapping.newapi_user_id}，跳过')
            return False

        used = int(quota['used_quota'] or 0)
        remaining = await credit_service.get_total_available_credits(db, user_id, app_code)
        target = _target_quota_for_remaining(used, remaining)
        try:
            await newapi_admin_client.set_user_quota(newapi_user_id=mapping.newapi_user_id, quota=target)
        except NewApiError as exc:
            log.warning(f'[CreditSync] set_user_quota 失败 user_id={user_id}: {exc!r}')
            return False
        log.info(
            f'[CreditSync] user_id={user_id} quota→账本剩余: used={used} remaining={remaining} target_quota={target}'
        )
        return True

    @staticmethod
    async def reconcile_user(
        db: AsyncSession,
        user_id: int,
        *,
        app_code: str = 'huanxing',
    ) -> dict:
        """§5A.5 单用户增量同步：读 used_quota 增量 → 回扣账本 → 重设 quota → 推进游标。

        返回 {status, consumed_credits, remaining_credits, used_quota}。status:
          ok / no_mapping / newapi_unreachable / newapi_no_user / no_delta。
        失败如实降级，不推进游标（下轮重试），绝不臆造扣减。
        """
        from backend.app.billing.service.credit_service import credit_service

        mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id, app_code)
        if not mapping:
            return {'status': 'no_mapping', 'user_id': user_id}
        try:
            quota = await newapi_admin_client.get_user_quota(mapping.newapi_user_id)
        except NewApiError as exc:
            log.warning(f'[CreditSync] reconcile 读 quota 失败 user_id={user_id}: {exc!r}')
            return {'status': 'newapi_unreachable', 'user_id': user_id}
        if not quota:
            return {'status': 'newapi_no_user', 'user_id': user_id}

        u_now = int(quota['used_quota'] or 0)
        cursor = int(mapping.last_synced_used_quota or 0)
        delta = u_now - cursor

        consumed = Decimal('0')
        if delta > 0:
            consumed = Decimal(delta) / Decimal(_rate())
            # 回扣账本，封顶在可用余额（deduct_credits 不足会抛 InsufficientCreditsError）；
            # 超出部分（new-api 本不该放行）即视为已用尽，游标照常推进。
            available = await credit_service.get_total_available_credits(db, user_id, app_code)
            to_deduct = min(consumed, available)
            if to_deduct > 0:
                await credit_service.deduct_credits(
                    db,
                    user_id,
                    to_deduct,
                    reference_type='llm_usage',
                    description=f'LLM 用量每小时回扣（new-api used_quota {cursor}→{u_now}）',
                    extra_data={'delta_quota': delta, 'newapi_user_id': mapping.newapi_user_id},
                    app_code=app_code,
                )

        # 重设 new-api 可用 = 账本剩余×RATE（同时收回账本已过期额度）
        remaining = await credit_service.get_total_available_credits(db, user_id, app_code)
        target = _target_quota_for_remaining(u_now, remaining)
        try:
            await newapi_admin_client.set_user_quota(newapi_user_id=mapping.newapi_user_id, quota=target)
        except NewApiError as exc:
            # quota 重设失败 → 不推进游标，避免「已回扣却未收口」的漂移，下轮重试
            log.warning(f'[CreditSync] reconcile set_quota 失败 user_id={user_id}（不推进游标）: {exc!r}')
            return {'status': 'newapi_unreachable', 'user_id': user_id, 'consumed_credits': consumed}

        # 推进游标（ORM 行级；本服务在 async_db_session.begin() 内调用，提交即落库）
        mapping.last_synced_used_quota = u_now
        mapping.last_synced_at = timezone.now()

        status = 'ok' if delta > 0 else 'no_delta'
        return {
            'status': status,
            'user_id': user_id,
            'consumed_credits': consumed,
            'remaining_credits': remaining,
            'used_quota': u_now,
        }

    @staticmethod
    async def reconcile_all() -> dict:
        """§5A.5 全量每小时同步：遍历所有 active 映射逐用户对账。

        **每用户独立事务**——单用户失败/异常回滚仅影响该用户，其余照常推进
        （avoid「一个用户的 deduct 异常拖垮整批」）。返回
        {total, ok, no_delta, skipped, failed, consumed_credits_total}。
        """
        from backend.database.db import async_db_session

        # 1) 单读会话取出所有 active 映射的 (user_id, app_code)
        async with async_db_session() as read_db:
            mappings: list[LlmNewapiUserMapping] = list(await llm_newapi_user_mapping_dao.get_all(read_db))
        targets = [(m.huanxing_user_id, m.app_code) for m in mappings if m.status == 'active']

        ok = no_delta = skipped = failed = 0
        consumed_total = Decimal('0')
        for user_id, app_code in targets:
            try:
                async with async_db_session.begin() as db:
                    result = await CreditSyncService.reconcile_user(db, user_id, app_code=app_code)
            except Exception as exc:  # noqa: BLE001 — 单用户异常隔离，不拖垮整批
                log.error(f'[CreditSync] reconcile_user 异常 user_id={user_id}: {exc!r}')
                failed += 1
                continue
            st = result['status']
            if st == 'ok':
                ok += 1
                consumed_total += result.get('consumed_credits') or Decimal('0')
            elif st == 'no_delta':
                no_delta += 1
            elif st == 'newapi_unreachable':
                failed += 1
            else:  # no_mapping / newapi_no_user
                skipped += 1
        summary = {
            'total': len(targets),
            'ok': ok,
            'no_delta': no_delta,
            'skipped': skipped,
            'failed': failed,
            'consumed_credits_total': consumed_total,
        }
        log.info(f'[CreditSync] reconcile_all 完成: {summary}')
        return summary


credit_sync_service = CreditSyncService()
