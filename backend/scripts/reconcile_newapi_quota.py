r"""一次性救急：把 new-api users.quota 按唤星账本权威重新补正。

背景：生产 new-api 构建（实测 117.72.92.229，v0.0.0）的 ManageUser(`/user/manage add_quota`)
**静默 no-op**（返回 success=True 但 quota 不变），而旧 `set_user_quota` 只用该端点
→ 注册赠额、入账推额度、每小时对账三条 quota 写入全部静默失效 → 新用户 quota 恒 0
（relay 报「用户额度不足 剩余额度 \$0」）。修复版 `set_user_quota` 已改自校验双机制
（UpdateUser→ManageUser + 回读校验），本脚本逐用户把 new-api 可用额度重设为
`used_quota + 账本剩余积分 × RATE`（= `credit_sync_service.sync_quota_to_balance`，
**只设 new-api quota、不扣账本**，幂等、账本权威）。

降级：new-api 不可达 / 无映射 / new-api 无此用户 → 跳过该用户并如实记录，不造假。

前置：生产后端已部署含修复版 `set_user_quota` 的版本（本脚本依赖其真正生效）。

用法（先 --dry-run 看报告，确认无误再真跑）：
    PYTHONPATH=backend uv run python -m backend.scripts.reconcile_newapi_quota --dry-run
    PYTHONPATH=backend uv run python -m backend.scripts.reconcile_newapi_quota
    # 定点修某手机号（尾号）匹配的用户：
    PYTHONPATH=backend uv run python -m backend.scripts.reconcile_newapi_quota --phone 18687200686

生产：DATABASE_URL 指向生产 huanxing 库；NEWAPI_ADMIN_* 指向生产 new-api。
"""

from __future__ import annotations

import argparse
import asyncio

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.app.newapi.credit_sync_service import _target_quota_for_remaining, credit_sync_service
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping


async def _target_user_ids(db: AsyncSession, phone: str | None) -> list[int] | None:
    """phone 给定 → 按手机号尾号匹配的 user_id 列表；否则 None（= 全部 active 映射）。"""
    if not phone:
        return None
    from backend.app.admin.model import User

    ids = list((await db.execute(sa.select(User.id).where(User.phone.like(f'%{phone}%')))).scalars().all())
    print(f'phone %{phone}% → {len(ids)} users: {ids}')
    return ids


async def _report_line(db: AsyncSession, mapping: LlmNewapiUserMapping) -> None:
    """dry-run：读当前 quota + 账本剩余，算目标，不写。"""
    from backend.app.billing.service.credit_service import credit_service

    try:
        q = await newapi_admin_client.get_user_quota(mapping.newapi_user_id)
    except NewApiError as exc:
        print(f'  user={mapping.huanxing_user_id} newapi={mapping.newapi_user_id} 读 quota 失败（跳过）: {exc!r}')
        return
    if not q:
        print(f'  user={mapping.huanxing_user_id} newapi={mapping.newapi_user_id} new-api 无此用户（跳过）')
        return
    used = int(q['used_quota'] or 0)
    remaining = await credit_service.get_total_available_credits(db, mapping.huanxing_user_id, mapping.app_code)
    target = _target_quota_for_remaining(used, remaining)
    drift = '  <== 需补正' if int(q['quota'] or 0) != target else ''
    print(
        f'  user={mapping.huanxing_user_id} newapi={mapping.newapi_user_id} '
        f'quota={q["quota"]} used={used} 账本剩余={remaining} 目标quota={target}{drift}'
    )


async def _run(*, dry_run: bool, phone: str | None) -> None:
    async with async_db_session() as db:
        user_ids = await _target_user_ids(db, phone)
        all_mappings = list(await llm_newapi_user_mapping_dao.get_all(db))
        mappings = [
            m for m in all_mappings if m.status == 'active' and (user_ids is None or m.huanxing_user_id in user_ids)
        ]
        print(f'active 映射 {len(mappings)} 个待处理（dry_run={dry_run}）')

        if dry_run:
            for m in mappings:
                await _report_line(db, m)
            await db.rollback()
            print('(dry-run) 未写任何内容')
            return

    # 真跑：逐用户独立事务调 sync_quota_to_balance（只设 new-api quota、不扣账本）
    ok = failed = 0
    for m in mappings:
        try:
            async with async_db_session.begin() as db:
                done = await credit_sync_service.sync_quota_to_balance(db, m.huanxing_user_id, app_code=m.app_code)
            if done:
                ok += 1
                print(f'  user={m.huanxing_user_id} newapi={m.newapi_user_id} ✅ quota 已按账本补正')
            else:
                failed += 1
                print(f'  user={m.huanxing_user_id} newapi={m.newapi_user_id} ⚠️ 跳过（无映射/不可达，见日志）')
        except Exception as exc:  # noqa: PERF203 — 单用户隔离，不拖垮整批
            failed += 1
            print(f'  user={m.huanxing_user_id} ❌ 异常: {exc!r}')
    print(f'完成：ok={ok} failed/skip={failed} total={len(mappings)}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='只报告不写')
    parser.add_argument('--phone', default=None, help='只修手机号（尾号）匹配的用户')
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run, phone=args.phone))


if __name__ == '__main__':
    main()
