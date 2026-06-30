"""PackageService 打包/缓存契约测试。

修复目标（两个叠加问题）：
1. 打包后从不回写 version.file_hash → 缓存判定 (file_hash == cached_hash) 永远未命中 →
   每次下载都重新打包。单 worker dev 下批量同步技能时连续重打包会阻塞事件循环。
2. zip 遍历/写盘同步执行在事件循环上。

两组覆盖：
- 纯函数 `_build_package_sync`（恒跑，无外部依赖）：真实 zip + 排除 hidden/.pyc/__pycache__ +
  原子 rename（不残留 .tmp）+ 同输入哈希稳定。
- 真实联调（真 PG，不可达则 skip，零 mock）：get_skill_package 首次打包回写 file_hash，
  第二次命中缓存不重建（文件 mtime 不变）。需要 export DATABASE_PORT=15432，事务末尾回滚不污染库。
"""

from __future__ import annotations

import uuid
import zipfile

from typing import TYPE_CHECKING

import pytest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.model import MarketplaceSkill, MarketplaceSkillVersion
from backend.app.marketplace.service.package_service import PackageService
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from pathlib import Path

# --------------------------------------------------------------------------- 纯函数（恒跑）


def _seed_skill_dir(root: Path) -> Path:
    """造一个最小技能源目录：含正文 + 一个资源文件 + 应被忽略的脏文件。"""
    skill_dir = root / 'skill'
    skill_dir.mkdir(parents=True)
    (skill_dir / 'SKILL.md').write_text('# 技能\n正文\n', encoding='utf-8')
    (skill_dir / 'ref.txt').write_text('reference\n', encoding='utf-8')
    # 以下三类必须被排除
    (skill_dir / '.DS_Store').write_text('junk', encoding='utf-8')
    (skill_dir / 'mod.pyc').write_text('bytecode', encoding='utf-8')
    pycache = skill_dir / '__pycache__'
    pycache.mkdir()
    (pycache / 'x.pyc').write_text('junk', encoding='utf-8')
    return skill_dir


def test_build_package_sync_produces_valid_zip_excluding_junk(tmp_path) -> None:
    skill_dir = _seed_skill_dir(tmp_path)
    package_path = tmp_path / 'out.zip'

    digest = PackageService._build_package_sync(skill_dir, package_path)

    assert package_path.exists()
    assert len(digest) == 64  # sha256 hex
    # 原子 rename：不残留 .tmp
    assert not (tmp_path / 'out.zip.tmp').exists()

    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
    assert names == {'SKILL.md', 'ref.txt'}  # hidden / .pyc / __pycache__ 全部排除


def test_build_package_sync_hash_stable_for_same_content(tmp_path) -> None:
    skill_dir = _seed_skill_dir(tmp_path)
    p1 = tmp_path / 'a.zip'
    p2 = tmp_path / 'b.zip'
    h1 = PackageService._build_package_sync(skill_dir, p1)
    h2 = PackageService._build_package_sync(skill_dir, p2)
    # 同一最终文件再哈希必须一致（缓存判定依赖此性质）
    assert PackageService._hash_file_sync(p1) == h1
    assert PackageService._hash_file_sync(p2) == h2


def test_build_package_sync_cleans_tmp_on_error(tmp_path) -> None:
    """源目录不存在 → 写 zip 时 os.walk 空但 zip 仍生成；真错误用不存在的目标父目录触发。"""
    skill_dir = _seed_skill_dir(tmp_path)
    bad_path = tmp_path / 'no_such_dir' / 'out.zip'  # 父目录不存在 → ZipFile 打开失败
    with pytest.raises(Exception):
        PackageService._build_package_sync(skill_dir, bad_path)
    assert not (tmp_path / 'no_such_dir' / 'out.zip.tmp').exists()


# --------------------------------------------------------------------------- 真实联调（真 PG）


def _db_reachable() -> bool:
    import socket

    from backend.core.conf import settings

    try:
        with socket.create_connection((settings.DATABASE_HOST, settings.DATABASE_PORT), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.asyncio
@pytest.mark.skipif(not _db_reachable(), reason='本地 PG 不可达（需 export DATABASE_PORT=15432）')
async def test_get_skill_package_writes_back_hash_and_hits_cache(tmp_path) -> None:
    skill_id = f'test/pkg-{uuid.uuid4().hex[:8]}'
    rel_repo = 'test-pkg-skill'
    # 真实 hub 源目录 + 隔离的缓存目录
    hub_root = tmp_path / 'hub'
    (hub_root / rel_repo).mkdir(parents=True)
    (hub_root / rel_repo / 'SKILL.md').write_text('# pkg\n正文\n', encoding='utf-8')
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()

    svc = PackageService()
    svc.hub_repo_path = hub_root
    svc.cache_dir = cache_dir

    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as db:
            db.add(MarketplaceSkill(skill_id=skill_id, name='pkg', repo_path=rel_repo, status='published'))
            db.add(MarketplaceSkillVersion(skill_id=skill_id, version='1.0.0', is_latest=True))
            await db.flush()

            # 首次：缓存未命中 → 打包 + 回写 file_hash
            path1, hash1 = await svc.get_skill_package(db, skill_id)
            assert path1.exists()
            assert len(hash1) == 64

            version = await marketplace_skill_version_dao.get_by_skill_and_version(db, skill_id, '1.0.0')
            assert version is not None
            assert version.file_hash == hash1, '打包后必须把 file_hash 回写（否则缓存永远未命中、每次重打包）'

            # 第二次：file_hash 已落库且与缓存一致 → 命中缓存、不重建（mtime 不变）
            mtime_after_first = path1.stat().st_mtime_ns
            path2, hash2 = await svc.get_skill_package(db, skill_id)
            assert path2 == path1
            assert hash2 == hash1
            assert path2.stat().st_mtime_ns == mtime_after_first, '第二次应命中缓存，不应重新打包'

            await db.rollback()
    finally:
        await engine.dispose()
