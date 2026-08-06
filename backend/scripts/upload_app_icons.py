"""运维（可重复执行）：把应用中心彩色品牌图标 SVG 上传到公共桶 ``app-icons/``。

工作台「应用中心」卡片图标走 ``hasn_app_catalog.icon_asset_uri``（图片优先于 token，
见 webui ``AppCard``/``lib/appIcons``）。本脚本复用预置头像同一条上传链路
（``s3_storage`` 公共桶 + ``write_bytes``），把图标 SVG 覆盖式（幂等）上传到公共桶固定路径
``app-icons/{app_id}.svg``，并打印各自的稳定 CDN 直读 URL —— 这些 URL 即
``hasn_app_catalog.icon_asset_uri`` 回填值（见迁移 ``backend/sql/hasn/migrations/
2026-06-22-app-catalog-icon-asset-uri.sql`` 等）。

图标唯一源：兄弟仓 ``hasn-node/webui/public/app-icons/{app_id}.svg``（随前端一起版本化）。

**自动扫描源目录所有 ``*.svg``**（不再维护硬编码应用名单）——新增应用只要把
``{app_id}.svg`` 放进该目录、再跑一次本脚本即上线，**杜绝「加了应用却忘了加名单
→ CDN 上没有该 svg → 卡片图标 404」**（这是历史上每次新应用图标 404 的根因）。

并做一次 **catalog 反查守卫**：列出 ``hasn_app_catalog.icon_asset_uri`` 引用了
``app-icons/{id}.svg`` 却在源目录缺失该 svg 的应用（= 上线即 404 的隐患），让缺口在
上传前/CI 阶段就暴露，而不是等用户点开应用才发现。

dev 与生产共用同一个七牛公共桶 ``hasn-pub``，故本地跑一次即写入共享桶、URL 两端通用。

零 fake：直接读真实 SVG、走真实公共桶；缺图/上传失败立即报错，不静默跳过。

用法：
    DATABASE_PORT=15432 uv run python -m backend.scripts.upload_app_icons --dry-run
    DATABASE_PORT=15432 uv run python -m backend.scripts.upload_app_icons
    DATABASE_PORT=15432 uv run python -m backend.scripts.upload_app_icons --check  # 只跑反查守卫，不上传
"""
from __future__ import annotations

import argparse
import asyncio

from pathlib import Path

from sqlalchemy import text

from backend.database.db import async_db_session
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.utils.file_ops import build_object_url, pick_public_storage, write_bytes

# scripts → backend → hasn-cloud-backend → huanxing-project（兄弟仓 hasn-node 在此层）
_REPO_PARENT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_DIR = _REPO_PARENT / 'hasn-node' / 'webui' / 'public' / 'app-icons'

# 公共桶对象前缀（与 icon_asset_uri 中的路径段一致）。
_OBJECT_PREFIX = 'app-icons'


def _discover_icons(source_dir: Path) -> dict[str, Path]:
    """扫描源目录所有 ``*.svg``，文件名（去扩展名）即 app_id。"""
    if not source_dir.is_dir():
        raise SystemExit(f'源图标目录不存在：{source_dir}')
    files = {path.stem: path for path in sorted(source_dir.glob('*.svg'))}
    if not files:
        raise SystemExit(f'源图标目录无任何 .svg：{source_dir}')
    return files


def _referenced_app_id(icon_asset_uri: str | None) -> str | None:
    """若 icon_asset_uri 指向 ``.../app-icons/{id}.svg`` 则返回 ``{id}``，否则 None。"""
    if not icon_asset_uri:
        return None
    marker = f'/{_OBJECT_PREFIX}/'
    idx = icon_asset_uri.find(marker)
    if idx < 0:
        return None
    tail = icon_asset_uri[idx + len(marker):]
    if not tail.endswith('.svg'):
        return None
    return tail[: -len('.svg')]


async def _catalog_referenced_ids(db) -> dict[str, str]:
    """catalog 中 icon_asset_uri 指向 ``app-icons/{id}.svg`` 的应用 → ``{app_id: uri}``。"""
    result = await db.execute(
        text('SELECT app_id, icon_asset_uri FROM hasn_app_catalog WHERE icon_asset_uri IS NOT NULL')
    )
    referenced: dict[str, str] = {}
    for _app_id, icon_asset_uri in result.all():
        ref = _referenced_app_id(icon_asset_uri)
        if ref is not None:
            referenced[ref] = icon_asset_uri
    return referenced


def _report_guard(available: set[str], referenced: dict[str, str]) -> list[str]:
    """反查守卫：返回「catalog 引用了但源目录缺失」的 app_id（会 404 的隐患）。"""
    missing_source = sorted(ref for ref in referenced if ref not in available)
    unreferenced = sorted(app_id for app_id in available if app_id not in referenced)

    if missing_source:
        print('⚠️  以下应用 catalog 引用了图标，但源目录缺对应 svg（CDN 上不存在 → 卡片图标 404）：')
        for app_id in missing_source:
            print(f'      - {app_id}: 缺 {DEFAULT_SOURCE_DIR / f"{app_id}.svg"}')
        print(f'    修法：在源目录补 {app_id}.svg 后重跑本脚本。')
    else:
        print('✅  反查守卫：所有 catalog 引用的图标都在源目录有 svg（无 404 隐患）。')

    if unreferenced:
        print(f'ℹ️  源目录有但 catalog 未引用（可能尚未注册/用 token，仅提示）：{unreferenced}')
    return missing_source


async def _run(source_dir: Path, *, dry_run: bool, check_only: bool) -> None:
    files = _discover_icons(source_dir)
    available = set(files)
    print(f'源目录发现 {len(files)} 个图标：{sorted(available)}')

    async with async_db_session() as db:
        referenced = await _catalog_referenced_ids(db)
        missing_source = _report_guard(available, referenced)

        if check_only:
            if missing_source:
                raise SystemExit(f'反查守卫失败：{missing_source} 缺源 svg（会 404）。')
            print('--check 通过（无 404 隐患）。')
            return

        storages = await s3_storage_dao.get_all(db)
        s3_storage = pick_public_storage(storages)
        if not s3_storage or getattr(s3_storage, 'access', 'private') != 'public':
            raise SystemExit('未找到公共 S3 存储配置（access=public），无法上传应用图标')
        print(f'公共桶: name={s3_storage.name!r} bucket={s3_storage.bucket!r} '
              f'access={s3_storage.access!r} cdn={s3_storage.cdn_domain!r}')

        for app_id, path in files.items():
            object_path = f'{_OBJECT_PREFIX}/{app_id}.svg'
            data = path.read_bytes()
            url = build_object_url(s3_storage, object_path)
            if dry_run:
                print(f'[dry-run] {app_id}: {path.name} -> {object_path} ({len(data)}B) => {url}')
                continue
            await write_bytes(s3_storage, object_path, data, 'image/svg+xml')
            print(f"  '{app_id}': '{url}',")

    print('完成（上面是回填用 URL 映射）。' if not dry_run else 'dry-run 结束（未写入）。')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='把应用中心彩色品牌图标自动上传到公共桶（扫描源目录，反查 catalog 守卫）'
    )
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SOURCE_DIR,
                        help=f'源 SVG 目录（默认 {DEFAULT_SOURCE_DIR}）')
    parser.add_argument('--dry-run', action='store_true', help='只打印不写入（仍跑反查守卫）')
    parser.add_argument('--check', action='store_true',
                        help='只跑 catalog 反查守卫并退出（有 404 隐患则非零退出，适合 CI）')
    args = parser.parse_args()
    asyncio.run(_run(args.source_dir.resolve(), dry_run=args.dry_run, check_only=args.check))


if __name__ == '__main__':
    main()
