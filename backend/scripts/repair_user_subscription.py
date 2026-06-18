"""一次性救急：为缺失订阅的用户补建免费订阅（按 tier 配置赠送积分）+ 同步 new-api quota。

背景：清库（清唤星库用户/订阅/映射，留 new-api）后，老用户重新登录走 `is_new_user=False`
分支——只补了 new-api 映射、**未调 `get_or_create_subscription`** → 账本（订阅/余额/流水）为空
→ `get_total_available_credits=0` → 每小时对账把 new-api quota 拉回 `used + 0`（可用归 0）。
本脚本对指定用户补 `get_or_create_subscription`（缺则建免费档：免费版月度赠送积分=tier 配置，
幂等已存在则 no-op）+ `sync_quota_to_balance`（令 new-api 可用 = 账本剩余×RATE）。

绝不臆造金额：免费档赠送额取 `subscription_tier(free)` 配置（huanxing=100 积分=$100）。

用法（先 --dry-run）：
    PYTHONPATH=backend uv run python -m backend.scripts.repair_user_subscription --phone 18687200686 --dry-run
    PYTHONPATH=backend uv run python -m backend.scripts.repair_user_subscription --phone 18687200686
    PYTHONPATH=backend uv run python -m backend.scripts.repair_user_subscription --user-id 114 115

生产：DATABASE_URL 指向生产 huanxing 库；NEWAPI_ADMIN_* 指向生产 new-api。
"""

from __future__ import annotations

import argparse
import asyncio

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.billing.service.credit_service import credit_service
from backend.app.newapi.credit_sync_service import credit_sync_service
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _resolve_user_ids(db: AsyncSession, phones: list[str], user_ids: list[int]) -> list[int]:
    ids = set(user_ids)
    for phone in phones:
        from backend.app.admin.model import User

        rows = list((await db.execute(sa.select(User.id).where(User.phone.like(f'%{phone}%')))).scalars().all())
        print(f'phone %{phone}% → users {rows}')
        ids.update(rows)
    return sorted(ids)


async def _run(*, phones: list[str], user_ids: list[int], dry_run: bool) -> None:
    async with async_db_session() as db:
        targets = await _resolve_user_ids(db, phones, user_ids)
    if not targets:
        print('无目标用户')
        return
    print(f'目标用户：{targets}（dry_run={dry_run}）')

    for uid in targets:
        async with async_db_session() as read_db:
            before = await credit_service.get_total_available_credits(read_db, uid)
        if dry_run:
            print(f'  user={uid} 账本可用积分(前)={before} —（dry-run）将 ensure 订阅 + sync quota')
            continue
        # 1) 补订阅（缺则建免费档赠送；幂等）—— 独立事务提交账本
        async with async_db_session.begin() as db:
            await credit_service.get_or_create_subscription(db, uid)
        async with async_db_session() as read_db:
            after = await credit_service.get_total_available_credits(read_db, uid)
        # 2) 同步 new-api quota = used + 账本剩余×RATE（只设 quota 不扣账本）
        async with async_db_session.begin() as db:
            synced = await credit_sync_service.sync_quota_to_balance(db, uid)
        print(f'  user={uid} 账本可用积分 {before}→{after}；new-api quota 同步={"✅" if synced else "⚠️跳过"}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phone', action='append', default=[], help='手机号（尾号）匹配，可多次')
    parser.add_argument('--user-id', type=int, action='append', default=[], dest='user_ids', help='用户 ID，可多次')
    parser.add_argument('--dry-run', action='store_true', help='只报告不写')
    args = parser.parse_args()
    asyncio.run(_run(phones=args.phone, user_ids=args.user_ids, dry_run=args.dry_run))


if __name__ == '__main__':
    main()
