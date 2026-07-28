"""分批迁移 Growth 存量 PII，默认 dry-run 且输出仅含计数和游标。

用法：

    DATABASE_PORT=15432 uv run python -m backend.scripts.migrate_growth_pii \
      --source contact --after-id 0

真实写入必须显式增加 ``--apply`` 和变更单号；每批独立提交，失败时从最后一个已打印
游标续跑。脚本不会清理旧明文列，旧列清理属于 S13 的独立授权动作。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from dataclasses import asdict

from backend.app.hasn_growth.service.pii_keyring import get_growth_pii_keyring
from backend.app.hasn_growth.service.pii_migration_service import (
    MigrationBatchResult,
    MigrationSource,
    growth_pii_migration_service,
)
from backend.database.db import async_db_session


async def _run(
    *,
    source: MigrationSource,
    after_id: int,
    batch_size: int,
    max_batches: int,
    apply: bool,
    change_ticket: str | None,
) -> int:
    if apply and not (change_ticket or '').strip():
        print('真实迁移必须提供 --change-ticket', file=sys.stderr)
        return 2

    cursor = after_id
    batches = 0
    totals = MigrationBatchResult(
        source_table=source,
        after_id=after_id,
        next_cursor=after_id,
        dry_run=not apply,
    )
    try:
        keyring = get_growth_pii_keyring()
    except Exception as exc:
        # 配置异常可能含密钥解析细节；这里只输出异常类型和安全游标。
        print(
            json.dumps(
                {
                    'status': 'failed',
                    'source_table': source,
                    'after_id': cursor,
                    'error_type': type(exc).__name__,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    while max_batches == 0 or batches < max_batches:
        try:
            async with async_db_session() as db:
                result = await growth_pii_migration_service.migrate_batch(
                    db,
                    keyring=keyring,
                    source_table=source,
                    after_id=cursor,
                    batch_size=batch_size,
                    dry_run=not apply,
                )
                if apply:
                    await db.commit()
                else:
                    await db.rollback()
        except Exception as exc:
            # 数据库异常文本可能带 SQL 参数；这里只输出异常类型和安全游标。
            print(
                json.dumps(
                    {
                        'status': 'failed',
                        'source_table': source,
                        'after_id': cursor,
                        'error_type': type(exc).__name__,
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 1

        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        totals.scanned += result.scanned
        totals.migrated += result.migrated
        totals.quarantined += result.quarantined
        totals.skipped += result.skipped
        totals.next_cursor = result.next_cursor
        batches += 1
        if result.scanned < batch_size or result.next_cursor == cursor:
            break
        cursor = result.next_cursor

    print(
        json.dumps(
            {
                'status': 'completed',
                'source_table': source,
                'change_ticket': change_ticket if apply else None,
                'batches': batches,
                **asdict(totals),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--source',
        choices=('contact', 'customer', 'form_submission'),
        required=True,
        help='本次迁移的源表',
    )
    parser.add_argument('--after-id', type=int, default=0, help='从该主键之后继续')
    parser.add_argument('--batch-size', type=int, default=100, help='每批 1..1000 行')
    parser.add_argument('--max-batches', type=int, default=0, help='最多批次数，0 表示跑到表尾')
    parser.add_argument('--apply', action='store_true', help='真实写入；默认只演练')
    parser.add_argument('--change-ticket', help='真实写入所需的生产变更单号')
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return asyncio.run(
        _run(
            source=args.source,
            after_id=args.after_id,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            apply=args.apply,
            change_ticket=args.change_ticket,
        )
    )


if __name__ == '__main__':
    raise SystemExit(main())
