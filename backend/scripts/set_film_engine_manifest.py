"""设置/更新 film（VideoClaw）下载式引擎包的 manifest 地址到平台默认配置（PDC）。

VideoClaw 引擎是 downloadable_local 形态：运营把按各架构构建的引擎包托管到对象存储后，
经本脚本（或 Admin「平台默认配置」页）把 manifest.json 的签名/CDN URL 下发到
PDC `node.film.package_manifest_url`；daemon 拉 PDC 后据此发现并下载引擎包。

绝不臆造地址：`--manifest-url` 必填，由运营从对象存储托管结果给出。覆盖式写入（仅改
`node.film.package_manifest_url` 一项，其余 PDC 字段原样保留），重算 revision 触发
daemon revision-diff 下拉。

用法（先 --dry-run 预览不写）：
    PYTHONPATH=backend uv run python -m backend.scripts.set_film_engine_manifest \
        --manifest-url https://hasn-pub-cdn.dcfuture.cn/huanxing/film/0.1.0/manifest.json --dry-run
    PYTHONPATH=backend uv run python -m backend.scripts.set_film_engine_manifest \
        --manifest-url https://hasn-pub-cdn.dcfuture.cn/huanxing/film/0.1.0/manifest.json

生产：在生产应用根运行（DATABASE_* 指向生产 huanxing 库）。
"""

from __future__ import annotations

import argparse
import asyncio

from backend.app.hasn.schema.hasn_platform_default_config import PlatformDefaultConfig
from backend.app.hasn.service.platform_default_config_service import (
    compute_revision,
    platform_default_config_service,
)
from backend.database.db import async_db_session


async def _run(manifest_url: str, dry_run: bool) -> int:
    async with async_db_session.begin() as db:
        config, rev_before = await platform_default_config_service.get_effective_config(db)
        data = config.model_dump(mode='json')
        film = data.setdefault('node', {}).setdefault('film', {})
        old = film.get('package_manifest_url', '')
        if old == manifest_url:
            print(f'film.package_manifest_url 已是 {manifest_url!r}，无需变更（revision={rev_before}）')
            return 0
        film['package_manifest_url'] = manifest_url
        new_config = PlatformDefaultConfig.model_validate(data)
        rev_after = compute_revision(new_config.model_dump(mode='json'))
        print(f'film.package_manifest_url: {old!r} -> {manifest_url!r}')
        print(f'revision: {rev_before} -> {rev_after}')
        if dry_run:
            print('[dry-run] 未写入')
            return 0
        resp = await platform_default_config_service.update_config(
            db, config=new_config, updated_by='ops:vc-p2-film'
        )
        print(f'已写入 PDC（revision={resp.revision}，updated_by={resp.updated_by}）')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='设置 film 引擎包 manifest 地址到 PDC')
    parser.add_argument('--manifest-url', required=True, help='引擎包 manifest.json 的 URL（http/https）')
    parser.add_argument('--dry-run', action='store_true', help='只预览不写入')
    args = parser.parse_args()
    if not args.manifest_url.startswith(('http://', 'https://')):
        parser.error('--manifest-url 必须是 http(s) URL')
    return asyncio.run(_run(args.manifest_url, args.dry_run))


if __name__ == '__main__':
    raise SystemExit(main())
