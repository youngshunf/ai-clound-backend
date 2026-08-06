"""一次性回填 marketplace_skill_version.content_hash（doc14 §五 B1 数据侧）。

背景：B1 给 marketplace_skill_version 加了 content_hash 列（DDL 已落），但存量行
content_hash 为 NULL——content_hash 只在同步时计算（github_sync），没跑同步前存量
不会自动填。在填上前 common_skills_revision 回落到 COALESCE(content_hash, file_hash,
version) 的 file_hash/version，功能不坏，但拿不到「内容指纹驱动更新」的真实信号。

本脚本复用 github_sync_service 的真实算法（_iter_skill_markdown + _parse_skill_markdown
→ compute_skill_content_hash），从**本地 hub clone 的原始 SKILL.md + 附件**算 content_hash，
按 skill_id 匹配 is_latest 版本行 UPDATE。等价于跑一次 github_sync 的 content_hash 计算，
但跳过 clone/翻译/下载——纯本地、快、可控、零 LLM 调用。

幂等：同内容重跑得到同一 content_hash（compute_skill_content_hash 规范化、零随机/时间）。
零 fake：源文件缺失/frontmatter 不合法/DB 无匹配行 → 跳过并计数，不写假值。

用法（SCRIPT = backend/sql/marketplace/migrations/2026-06-16-backfill-skill-content-hash.py）：
    # dry-run（默认，只统计不写库）
    DATABASE_PORT=15432 .venv/bin/python "$SCRIPT" --hub-path /abs/path/to/hasn-hub
    # 真跑（加 --execute）
    DATABASE_PORT=15432 .venv/bin/python "$SCRIPT" --hub-path /abs/path/to/hasn-hub --execute

生产：部署最新代码后，可用本脚本立即回填（否则等下次 github_sync/webhook 自动填）。
"""

from __future__ import annotations

import argparse
import asyncio

from pathlib import Path

from sqlalchemy import text

from backend.app.marketplace.service.github_sync_service import github_sync_service
from backend.database.db import async_db_session


async def _backfill(hub_path: Path, *, execute: bool) -> None:
    # 指向本地 hub clone，禁用 git（git_commit_hash 走 None 分支），纯读文件算 hash。
    github_sync_service.local_path = str(hub_path)
    github_sync_service.repo = None

    skill_files = github_sync_service._iter_skill_markdown(hub_path)
    print(f'扫描到 {len(skill_files)} 个 SKILL.md（{hub_path}）')

    matched = updated = unchanged = no_row = parse_err = 0
    async with async_db_session() as db:
        for skill_md in skill_files:
            try:
                parsed = await github_sync_service._parse_skill_markdown(skill_md)
            except ValueError as exc:
                parse_err += 1
                print(f'  ⚠️ 解析跳过 {skill_md}: {exc}')
                continue

            skill_id = parsed['skill_id']
            content_hash = parsed['content_hash']

            row = (
                await db.execute(
                    text(
                        'SELECT content_hash FROM hasn_marketplace.marketplace_skill_version '
                        'WHERE skill_id = :sid AND is_latest = true'
                    ),
                    {'sid': skill_id},
                )
            ).first()
            if row is None:
                no_row += 1
                continue
            matched += 1
            if row.content_hash == content_hash:
                unchanged += 1
                continue
            if execute:
                await db.execute(
                    text(
                        'UPDATE hasn_marketplace.marketplace_skill_version '
                        'SET content_hash = :h, updated_time = now() '
                        'WHERE skill_id = :sid AND is_latest = true'
                    ),
                    {'h': content_hash, 'sid': skill_id},
                )
            updated += 1

        if execute:
            await db.commit()

    mode = '真跑(已提交)' if execute else 'DRY-RUN(未写库)'
    print(
        f'[{mode}] 匹配 is_latest 行={matched} | 需更新={updated} | 已是最新={unchanged} '
        f'| DB 无匹配行={no_row} | 解析跳过={parse_err}'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description='回填 skill_version.content_hash（doc14 §B1 数据侧）')
    ap.add_argument('--hub-path', required=True, help='本地 hasn-hub clone 的绝对路径')
    ap.add_argument('--execute', action='store_true', help='真跑写库（默认 dry-run）')
    args = ap.parse_args()
    asyncio.run(_backfill(Path(args.hub_path).resolve(), execute=args.execute))


if __name__ == '__main__':
    main()
