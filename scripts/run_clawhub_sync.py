#!/usr/bin/env python3
"""一次性运行 ClawHub 技能同步（脱离 HTTP 接口直接调 service，避免长任务被网关超时）。

为什么要独立脚本：全量 downloads>100 同步会逐个走 LLM 翻译，耗时几十分钟，
远超 admin API 的 HTTP 超时；直接调 service 层、配合服务器端 ``setsid`` 脱离 SSH 跑，
是生产落地这批技能的稳妥方式。downloads 阈值 / 50G 磁盘闸 / top-N 上限均沿用
``clawhub_sync_service`` 的配置（也可用下方命令行参数本次覆盖）。

用法（在 backend 目录下，用 uv 跑）：
    # 先评估：只统计 downloads>100 的命中数量 + 预估占用，不下载不翻译不落库
    uv run python scripts/run_clawhub_sync.py --dry-run --min-downloads 100

    # 再全量同步 downloads>100（不截断 top-N，磁盘到 50G 自动暂停）
    uv run python scripts/run_clawhub_sync.py --min-downloads 100 --limit 0

    # 指定技能子集
    uv run python scripts/run_clawhub_sync.py --skill-id some-slug --skill-id other-slug
"""

import argparse
import asyncio
import json
import sys

from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.app.marketplace.service.clawhub_sync_service import clawhub_sync_service
from backend.database.db import async_db_session


async def main() -> int:
    parser = argparse.ArgumentParser(description='Run ClawHub skill sync (threshold + disk cap)')
    parser.add_argument('--dry-run', action='store_true', help='只评估不落库：返回命中数量 + 占用预估')
    parser.add_argument(
        '--min-downloads', type=int, default=None, help='下载量阈值：只同步 downloads 严格大于该值（默认用配置）'
    )
    parser.add_argument('--limit', type=int, default=None, help='top-N 上限（默认用配置；0=不截断）')
    parser.add_argument('--skill-id', action='append', dest='skill_ids', default=None, help='限定到某些 slug（可重复）')
    args = parser.parse_args()

    async with async_db_session() as db:
        result = await clawhub_sync_service.sync_from_clawhub(
            db,
            force=True,
            skill_ids=args.skill_ids,
            limit=args.limit,
            min_downloads=args.min_downloads,
            dry_run=args.dry_run,
        )

    print('=== ClawHub sync result ===')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
