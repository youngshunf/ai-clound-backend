"""一次性运维：把 10 个应用中心彩色品牌图标 SVG 上传到公共桶 ``app-icons/``。

背景：工作台「应用中心」卡片图标走 ``hasn_app_catalog.icon_asset_uri``（图片优先于 token，
见 ``WorkbenchAppCard``）。此前除 deck 外 10 个内置应用 ``icon_asset_uri`` 为 NULL、前端落
单色兜底图标。本脚本复用预置头像同一条上传链路（``s3_storage`` 公共桶 + ``write_bytes``），
把 10 个 SVG 上传到公共桶固定路径 ``app-icons/{app_id}.svg``（覆盖式、幂等），并打印各自的
稳定 CDN 直读 URL —— 把这些 URL 回填到 ``hasn_app_catalog.icon_asset_uri``（见配套迁移
``backend/sql/hasn/migrations/2026-06-22-app-catalog-icon-asset-uri.sql``）。

dev 与生产共用同一个七牛公共桶 ``hasn-pub``，故本地跑一次即写入共享桶、URL 两端通用，
迁移可直接用这些固定 URL 回填两端 DB。

零 fake：直接读真实 SVG、走真实公共桶；缺图/上传失败立即报错，不静默跳过。

源 SVG：兄弟仓 ``hasn-node/webui/public/app-icons/{app_id}.svg``（图标唯一源，随前端一起版本化）。

用法：
    DATABASE_PORT=15432 uv run python -m backend.scripts.upload_app_icons --dry-run
    DATABASE_PORT=15432 uv run python -m backend.scripts.upload_app_icons
"""
from __future__ import annotations

import argparse
import asyncio

from pathlib import Path

from backend.database.db import async_db_session
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.utils.file_ops import build_object_url, pick_public_storage, write_bytes

# 11 个内置应用（deck 已有图标、不覆盖）。app_id 即源 SVG 文件名 + 公共桶对象名。
APP_IDS = (
    'knowledge',
    'community',
    'publish',
    'growth',
    'creator',
    'designsystem',
    'film',
    'copilot',
    'plan',
    'hasn_task',
    'reel',
    'quant',
)

# scripts → backend → huanxing-cloud-backend → huanxing-project（兄弟仓 hasn-node 在此层）
_REPO_PARENT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_DIR = _REPO_PARENT / 'hasn-node' / 'webui' / 'public' / 'app-icons'


async def _run(source_dir: Path, *, dry_run: bool) -> None:
    files = {app_id: source_dir / f'{app_id}.svg' for app_id in APP_IDS}
    missing = [str(p) for p in files.values() if not p.is_file()]
    if missing:
        raise SystemExit(f'源图标缺失，无法上传：{missing}')

    async with async_db_session() as db:
        storages = await s3_storage_dao.get_all(db)
        s3_storage = pick_public_storage(storages)
        if not s3_storage or getattr(s3_storage, 'access', 'private') != 'public':
            raise SystemExit('未找到公共 S3 存储配置（access=public），无法上传应用图标')
        print(f'公共桶: name={s3_storage.name!r} bucket={s3_storage.bucket!r} '
              f'access={s3_storage.access!r} cdn={s3_storage.cdn_domain!r}')

        for app_id, path in files.items():
            object_path = f'app-icons/{app_id}.svg'
            data = path.read_bytes()
            url = build_object_url(s3_storage, object_path)
            if dry_run:
                print(f'[dry-run] {app_id}: {path.name} -> {object_path} ({len(data)}B) => {url}')
                continue
            await write_bytes(s3_storage, object_path, data, 'image/svg+xml')
            print(f"  '{app_id}': '{url}',")

    print('完成（上面是回填用 URL 映射）。' if not dry_run else 'dry-run 结束（未写入）。')


def main() -> None:
    parser = argparse.ArgumentParser(description='上传 10 个应用中心彩色品牌图标到公共桶')
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SOURCE_DIR,
                        help=f'源 SVG 目录（默认 {DEFAULT_SOURCE_DIR}）')
    parser.add_argument('--dry-run', action='store_true', help='只打印不写入')
    args = parser.parse_args()
    asyncio.run(_run(args.source_dir.resolve(), dry_run=args.dry_run))


if __name__ == '__main__':
    main()
