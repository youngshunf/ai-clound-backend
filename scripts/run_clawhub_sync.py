#!/usr/bin/env python3
"""一次性运行 ClawHub 技能同步（脱离 HTTP 接口直接调 service，避免长任务被网关超时）。

为什么要独立脚本：全量 downloads>100 同步会逐个走 LLM 翻译，耗时几十分钟，
远超 admin API 的 HTTP 超时；直接调 service 层、配合服务器端 ``setsid`` 脱离 SSH 跑，
是生产落地这批技能的稳妥方式。downloads 阈值 / 50G 磁盘闸 / top-N 上限均沿用
``clawhub_sync_service`` 的配置（也可用下方命令行参数本次覆盖）。

用法（在 backend 目录下，用 uv 跑）：
    # 先评估：只统计 downloads>100 的命中数量 + 预估占用，不下载不翻译不落库
    uv run python scripts/run_clawhub_sync.py --dry-run --min-downloads 100

    # 大批量灌库推荐：名称/描述照翻（批量），正文原文填充（零正文 LLM），分批提交可续传
    uv run python scripts/run_clawhub_sync.py --min-downloads 100 --limit 0 \
        --no-translate-body --resume

    # 按真实人气取前 1 万：只收 downloads>0 或 stars>0 的，按 (下载,star) 降序截前 10000，
    # 不会用更新时间凑数补满 0/0 冷门技能（--min-downloads 0 保留"不卡死载，全收再排序"语义）
    uv run python scripts/run_clawhub_sync.py --min-downloads 0 --limit 10000 \
        --require-engagement --no-translate-body --resume

    # 完整翻译（含正文双语，慢且费 LLM——仅小批量/补译时用）
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
    parser.add_argument(
        '--no-translate-body', dest='translate_body', action='store_false', default=True,
        help='正文不翻译、原文填充（零正文 LLM）；名称/描述仍批量翻译。大批量灌库推荐。'
    )
    parser.add_argument(
        '--resume', action='store_true',
        help='跳过库中已存在的 clawhub slug（重跑只补未同步的，不重复翻译/下载）'
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
            force=True,
            skill_ids=args.skill_ids,
            limit=args.limit,
            min_downloads=args.min_downloads,
            dry_run=args.dry_run,
            translate_body=args.translate_body,
            batch_commit_size=args.batch_commit_size,
            resume=args.resume,
            require_engagement=args.require_engagement,
        )

    print('=== ClawHub sync result ===')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
