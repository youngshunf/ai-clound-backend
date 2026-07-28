"""分批迁移 Growth 历史项目归属，默认 dry-run 并输出监控可消费 JSON。

用法：

    DATABASE_PORT=15432 uv run python -m backend.scripts.migrate_growth_projects \
      --after-user-id 0 --batch-size 50

真实写入必须显式增加 ``--apply`` 和变更单号。游标只在一个 Owner 的事务成功提交后
推进；瞬时数据库错误按同一 Owner 重试，永久错误停止并保留最后安全游标。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from dataclasses import asdict
from typing import Any

import sqlalchemy as sa

from sqlalchemy.exc import DBAPIError

from backend.app.hasn_growth.service.project_migration_service import (
    growth_project_migration_service,
)
from backend.database.db import async_db_session

_TRANSIENT_SQLSTATES = frozenset({'40001', '40P01', '55P03', '57014'})


def _is_transient(exc: BaseException) -> bool:
    if not isinstance(exc, DBAPIError):
        return False
    if exc.connection_invalidated:
        return True
    sqlstate = getattr(exc.orig, 'sqlstate', None) or getattr(
        exc.orig, 'pgcode', None
    )
    return bool(
        sqlstate
        and (
            str(sqlstate) in _TRANSIENT_SQLSTATES
            or str(sqlstate).startswith('08')
        )
    )


async def _next_owner_ids(
    *,
    after_user_id: int,
    batch_size: int,
) -> list[int]:
    async with async_db_session() as db:
        rows = await db.execute(
            sa.text(
                """
                SELECT user_id
                FROM (
                    SELECT user_id FROM hasn_growth.lead_ref
                    UNION
                    SELECT user_id FROM hasn_growth.customer
                    UNION
                    SELECT user_id FROM hasn_growth.opportunity
                    UNION
                    SELECT user_id FROM hasn_growth.outreach_message
                    UNION
                    SELECT user_id FROM hasn_growth.activity
                    UNION
                    SELECT user_id FROM hasn_growth.form_submission
                ) AS candidates
                WHERE user_id > :after_user_id
                ORDER BY user_id
                LIMIT :batch_size
                """
            ),
            {
                'after_user_id': after_user_id,
                'batch_size': batch_size,
            },
        )
        return [int(value) for value in rows.scalars()]


async def _migrate_one(
    *,
    user_id: int,
    apply: bool,
    change_ticket: str,
    max_retries: int,
) -> dict[str, Any]:
    attempt = 0
    while True:
        try:
            async with async_db_session() as db:
                result = await growth_project_migration_service.migrate_owner(
                    db,
                    user_id=user_id,
                    dry_run=not apply,
                    change_ticket=change_ticket,
                )
                if apply:
                    await db.commit()
                else:
                    await db.rollback()
            return {
                **asdict(result),
                'attempts': attempt + 1,
            }
        except Exception as exc:  # ruff: ignore[try-except-in-loop]
            attempt += 1
            if attempt > max_retries or not _is_transient(exc):
                raise
            await asyncio.sleep(min(2**attempt, 10))


async def _shadow_one(*, user_id: int, sample_size: int) -> dict[str, Any]:
    async with async_db_session() as db:
        return await growth_project_migration_service.build_shadow_report(
            db,
            user_id=user_id,
            sample_size=sample_size,
        )


async def _run(
    *,
    after_user_id: int,
    owner_user_id: int | None,
    batch_size: int,
    max_batches: int,
    max_retries: int,
    apply: bool,
    change_ticket: str | None,
    shadow_only: bool,
    sample_size: int,
) -> int:
    if apply and not (change_ticket or '').strip():
        print('真实迁移必须提供 --change-ticket', file=sys.stderr)
        return 2
    if not 1 <= batch_size <= 1000:
        print('--batch-size 必须在 1..1000', file=sys.stderr)
        return 2
    if not 0 <= max_retries <= 10:
        print('--max-retries 必须在 0..10', file=sys.stderr)
        return 2

    cursor = after_user_id
    batches = 0
    processed = 0
    quarantined = 0
    shadow_failures = 0
    while max_batches == 0 or batches < max_batches:
        owner_ids = (
            [owner_user_id]
            if owner_user_id is not None and batches == 0
            else (
                []
                if owner_user_id is not None
                else await _next_owner_ids(
                    after_user_id=cursor,
                    batch_size=batch_size,
                )
            )
        )
        if not owner_ids:
            break
        for user_id in owner_ids:
            try:
                payload = (
                    await _shadow_one(user_id=user_id, sample_size=sample_size)
                    if shadow_only
                    else await _migrate_one(
                        user_id=user_id,
                        apply=apply,
                        change_ticket=change_ticket or 'DRY-RUN',
                        max_retries=max_retries,
                    )
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            'status': 'failed',
                            'safe_after_user_id': cursor,
                            'user_id': user_id,
                            'error_type': type(exc).__name__,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
                return 1
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            processed += 1
            quarantined += int(payload.get('quarantined', 0))
            shadow_failures += int(payload.get('status') == 'fail')
            cursor = user_id
        batches += 1
        if owner_user_id is not None or len(owner_ids) < batch_size:
            break

    print(
        json.dumps(
            {
                'status': (
                    'failed'
                    if shadow_only and shadow_failures
                    else 'completed'
                ),
                'mode': (
                    'shadow'
                    if shadow_only
                    else ('apply' if apply else 'dry-run')
                ),
                'change_ticket': change_ticket if apply else None,
                'batches': batches,
                'processed_owners': processed,
                'quarantined': quarantined,
                'shadow_failures': shadow_failures,
                'next_cursor': cursor,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if shadow_only and shadow_failures else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--after-user-id',
        type=int,
        default=0,
        help='从该 Owner user_id 之后续跑',
    )
    parser.add_argument(
        '--owner-user-id',
        type=int,
        help='只演练或迁移一个 Owner，用于脱敏样本复核',
    )
    parser.add_argument('--batch-size', type=int, default=50, help='每批 Owner 数')
    parser.add_argument(
        '--max-batches',
        type=int,
        default=0,
        help='最多批次数，0 表示跑到候选尾部',
    )
    parser.add_argument(
        '--max-retries',
        type=int,
        default=3,
        help='每个 Owner 的瞬时数据库错误重试次数',
    )
    parser.add_argument('--apply', action='store_true', help='真实写入；默认 dry-run')
    parser.add_argument('--change-ticket', help='真实写入所需的生产变更单号')
    parser.add_argument(
        '--shadow-only',
        action='store_true',
        help='只输出影子对比，发现差异时退出码为 1',
    )
    parser.add_argument(
        '--sample-size',
        type=int,
        default=20,
        help='影子报告最多输出的差异稳定键数量',
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return asyncio.run(
        _run(
            after_user_id=args.after_user_id,
            owner_user_id=args.owner_user_id,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            max_retries=args.max_retries,
            apply=args.apply,
            change_ticket=args.change_ticket,
            shadow_only=args.shadow_only,
            sample_size=args.sample_size,
        )
    )


if __name__ == '__main__':
    raise SystemExit(main())
