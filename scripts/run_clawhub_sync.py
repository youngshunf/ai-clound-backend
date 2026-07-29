#!/usr/bin/env python3
"""一次性运行 ClawHub 技能元数据同步。

全量目录同步可能超过 HTTP 网关超时，因此保留直接调用 service 的运维入口。任务只读取
ClawHub 列表、详情和版本文件清单，不下载 ZIP、不解压到服务器。

用法（在 backend 目录下，用 uv 跑）：
    # 先评估：统计命中数量和预计元数据请求数，不落库
    uv run python scripts/run_clawhub_sync.py --dry-run --min-downloads 100

    # 大批量增量同步
    uv run python scripts/run_clawhub_sync.py --min-downloads 100 --limit 0 --resume

    # 按真实人气取前 1 万：只收 downloads>0 或 stars>0 的，按 (下载,star) 降序截前 10000，
    # 不会用更新时间凑数补满 0/0 冷门技能
    uv run python scripts/run_clawhub_sync.py --min-downloads 0 --limit 10000 \
        --require-engagement --resume

    # 指定技能子集；重名时必须写 owner/slug
    uv run python scripts/run_clawhub_sync.py --skill-id alice/demo --skill-id bob/demo
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
    parser = argparse.ArgumentParser(description='运行 ClawHub 技能元数据同步')
    parser.add_argument('--dry-run', action='store_true', help='只评估不落库：返回命中数量与预计请求数')
    parser.add_argument(
        '--min-downloads', type=int, default=None, help='下载量阈值：只同步 downloads 严格大于该值（默认用配置）'
    )
    parser.add_argument('--limit', type=int, default=None, help='top-N 上限（默认用配置；0=不截断）')
    parser.add_argument(
        '--skill-id',
        action='append',
        dest='skill_ids',
        default=None,
        help='限定到某些 owner/slug；裸 slug 必须全局唯一（可重复）',
    )
    parser.add_argument('--force', action='store_true', help='忽略增量门控，重新处理全部命中元数据')
    parser.add_argument(
        '--resume', action='store_true',
        help='跳过库中已存在的稳定身份（重跑只补未同步记录）'
    )
    parser.add_argument(
        '--batch-commit-size', type=int, default=50,
        help='每处理 N 个技能提交一次（崩溃只丢最近一批、进度可见；默认 50）'
    )
    parser.add_argument(
        '--require-engagement', action='store_true',
        help='只同步"有真实人气"的技能（downloads>0 或 stars>0），丢弃 0/0 占位技能。'
             '取前 N 个真实人气技能时配 --limit 用，避免按更新时间凑数补满冷门技能。'
    )
    args = parser.parse_args()

    async with async_db_session() as db:
        result = await clawhub_sync_service.sync_from_clawhub(
            db,
            force=args.force,
            skill_ids=args.skill_ids,
            limit=args.limit,
            min_downloads=args.min_downloads,
            dry_run=args.dry_run,
            batch_commit_size=args.batch_commit_size,
            resume=args.resume,
            require_engagement=args.require_engagement,
        )

    print('=== ClawHub sync result ===')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
