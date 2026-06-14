#!/usr/bin/env python3
"""一次性运行 GitHub 技能全量同步（force=True，脱离 HTTP 接口直接调 service，避免网关超时）。

为什么要独立脚本：admin ``POST /marketplace/admin/sync/github`` 端点在请求内**同步**跑，
force 全量会重扫 ``huanxing-skills/*`` + ``github`` 子模块技能、逐个回填正文(body_en)与
文件清单(files)、并对变更正文批量 LLM 翻译(body_zh)，耗时远超 HTTP 超时。直接调 service
层、配合服务器端 ``setsid`` 脱离 SSH 跑，是把这两个目录的真实技能（正文+文件清单）灌进
marketplace 数据库的稳妥方式。

典型场景：webhook 触发的是**增量**同步（只处理本次 push 改动到的技能），子模块里的
github 技能 / 历史入库时尚无正文捕获能力的 huanxing 技能不会被重新处理 → 库里只有描述、
没有正文。用本脚本做一次全量回填。

body_en（原文，源语言侧）+ files + 元数据无需 LLM 必定回填；body_zh（译文）走变更门控 +
LLM，new-api 过载(503)时部分失败但**非致命**（技能仍落库）。大批量回填推荐 ``--no-translate-body``
（正文零 LLM、秒级完成），待 LLM 健康再用 admin ``/retranslate`` 或不带该参数重跑补译。

用法（在 backend 目录下，用 uv 跑）：
    # 大批量回填推荐：正文原文填充（零正文 LLM），名称/描述仍走变更门控批量翻译
    uv run python scripts/run_github_sync.py --no-translate-body

    # 完整翻译（含正文双语，慢且费 LLM——仅小批量/补译时用）
    uv run python scripts/run_github_sync.py
"""

import argparse
import asyncio
import json
import sys

from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.app.marketplace.service.github_sync_service import github_sync_service
from backend.database.db import async_db_session


async def main() -> int:
    parser = argparse.ArgumentParser(description='Run full GitHub skill sync (force=True)')
    parser.add_argument(
        '--no-translate-body', dest='translate_body', action='store_false', default=True,
        help='正文不翻译、原文填充（零正文 LLM）；名称/描述仍批量翻译。大批量回填推荐。'
    )
    args = parser.parse_args()

    # sync_from_github 内部最终 commit；用 .begin() 包裹与 webhook 后台同步一致，确保落库。
    async with async_db_session.begin() as db:
        result = await github_sync_service.sync_from_github(
            db,
            force=True,
            translate_body=args.translate_body,
        )

    print('=== GitHub sync result ===')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
