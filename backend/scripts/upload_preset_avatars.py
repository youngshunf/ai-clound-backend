"""一次性运维：把 12 个预置头像（缩放到 256×256）上传到公共桶 ``avatars/preset/``。

背景：``GET /api/v1/hasn/app/profile/preset-avatars`` 把头像 URL 指向公共桶
``avatars/preset/avatar-{NN}.png``（见 ``app/hasn/api/v1/app/profile.py``），但桶里
此前只成功落了 2 张（avatar-02 / avatar-07），其余 10 张 404，完善个人信息引导页
大量头像显示不出来。本脚本复用生产同一条上传链路（``s3_storage`` 公共桶 + 预签名 PUT
``write_bytes``），把全部 12 张统一缩放到 256×256 后重新上传（覆盖式、幂等）。

零 fake：直接读真实源图、走真实公共桶；缺图/上传失败立即报错，不静默跳过。

源图：默认取兄弟仓 ``hasn-node/webui/public/avatars/preset/avatar-{NN}.png``（同一套
预置头像的唯一源）。可用 ``--source-dir`` 覆盖。

用法（本地 dev DB 与生产共用同一个七牛公共桶 ``hasn-pub``，本地跑一次即写入共享桶）：
    DATABASE_PORT=15432 uv run python -m backend.scripts.upload_preset_avatars --dry-run
    DATABASE_PORT=15432 uv run python -m backend.scripts.upload_preset_avatars
"""
from __future__ import annotations

import argparse
import asyncio
import io

from pathlib import Path

from PIL import Image

from backend.database.db import async_db_session
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.utils.file_ops import build_object_url, pick_public_storage, write_bytes

PRESET_COUNT = 12
DEFAULT_SIZE = 256
# scripts → backend → huanxing-cloud-backend → huanxing-project（兄弟仓 hasn-node 在此层）
_REPO_PARENT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_DIR = _REPO_PARENT / 'hasn-node' / 'webui' / 'public' / 'avatars' / 'preset'


def _render_png(path: Path, size: int) -> bytes:
    """读取源 PNG，缩放到 size×size（LANCZOS 高质量重采样），编码为 PNG 字节。"""
    with Image.open(path) as source_image:
        image = source_image.convert('RGBA')
        if image.size != (size, size):
            image = image.resize((size, size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG', optimize=True)
        return buffer.getvalue()


async def _run(source_dir: Path, size: int, dry_run: bool) -> None:
    files = [source_dir / f'avatar-{i:02d}.png' for i in range(1, PRESET_COUNT + 1)]
    missing = [f for f in files if not f.is_file()]
    if missing:
        raise SystemExit(f'源头像缺失，无法上传：{[str(f) for f in missing]}')

    async with async_db_session() as db:
        storages = await s3_storage_dao.get_all(db)
        s3_storage = pick_public_storage(storages)
        if not s3_storage:
            raise SystemExit('未找到公共 S3 存储配置（access=public），无法上传预置头像')
        print(f'公共桶: name={s3_storage.name!r} bucket={s3_storage.bucket!r} '
              f'access={s3_storage.access!r} cdn={s3_storage.cdn_domain!r}')

        for i, path in enumerate(files, start=1):
            object_path = f'avatars/preset/avatar-{i:02d}.png'
            data = _render_png(path, size)
            url = build_object_url(s3_storage, object_path)
            if dry_run:
                print(f'[dry-run] 将上传 {path.name} -> {object_path} ({len(data)} bytes) => {url}')
                continue
            await write_bytes(s3_storage, object_path, data, 'image/png')
            print(f'已上传 {path.name} -> {object_path} ({len(data)} bytes) => {url}')

    print('完成。' if not dry_run else 'dry-run 结束（未写入）。')


def main() -> None:
    parser = argparse.ArgumentParser(description='上传 12 个预置头像（256×256）到公共桶')
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SOURCE_DIR,
                        help=f'源头像目录（默认 {DEFAULT_SOURCE_DIR}）')
    parser.add_argument('--size', type=int, default=DEFAULT_SIZE, help='输出边长像素（默认 256）')
    parser.add_argument('--dry-run', action='store_true', help='只打印不写入')
    args = parser.parse_args()
    asyncio.run(_run(args.source_dir.resolve(), args.size, args.dry_run))


if __name__ == '__main__':
    main()
