"""云端侧公共技能共享目录 reconciler（doc11 §5.3.5 / §6 B3）。

把 is_common+published 公共技能物化到与云端 hermes 同机的共享目录
``{shared_skills_root}/common/``，跨所有 owner 全局去重一份；hermes 经
``skills.external_dirs`` 消费（B1，hermes-runtime 侧改动），per-profile 不再重复下载。

跨组件共享目录契约（三方 writer——本 reconciler / daemon CommonSkillStore /
hermes provisioning——必须一致，勿单方改动）::

    <shared_skills_root>/common/
    ├── .index.json     # {"revision": str, "skills": {"<skill_id>": {"slug": str,
    │                   #   "fingerprint": str}}, "updated_at": ISO8601}
    ├── .lock           # O_CREAT|O_EXCL 抢锁；重试至 60s；mtime 超 10 分钟视为 stale 可删除重抢
    └── skills/<slug>/SKILL.md + 附属文件

- slug = skill_id 最后一个 ``/`` 段（与 hermes provisioning ``_skill_slug`` 一致），
  拒绝含 ``\\`` / ``..`` / NUL / 空段。
- 增量：index 中 fingerprint 一致且 ``skills/<slug>/SKILL.md`` 存在 → 跳过（kept）；
  否则重下重物化（先解压到临时目录再整目录替换，持锁期间做）。
- 下架 prune（评审 D6）：index 里有、云端清单里没有的 skill_id → 删 ``skills/<slug>/``
  + index 条目。只删 index 管理过的，不碰手装目录。
- 全部技能成功才把新 revision 写进 .index.json；部分失败保留旧 revision（下轮重试），
  但成功技能的条目先记入 index（fingerprint 已一致下轮不重下）。
- 零 fake：下载/解压失败如实 log + 计入失败清单，绝不写假成功。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
import zipfile

from collections.abc import Awaitable, Callable
from datetime import datetime
from datetime import timezone as dt_timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.service.common_skills_service import (
    get_common_skill_snapshot,
    get_skills_content_fingerprints,
)
from backend.app.marketplace.service.package_service import package_service
from backend.common.log import log

# 锁契约常量（与 daemon CommonSkillStore 一致，勿单方改动）
LOCK_TIMEOUT_SECONDS = 60
LOCK_STALE_SECONDS = 600
_LOCK_POLL_SECONDS = 1.0

# 内网/CDN 拉包超时（reconciler 与市场同机，正常远快于此）
_FETCH_TIMEOUT_SECONDS = 60.0

FetchZip = Callable[[AsyncSession, str], Awaitable[bytes]]


def _skill_slug(skill_id: str) -> str | None:
    """slug 派生 = skill_id 最后一个 ``/`` 段（与 hermes provisioning ``_skill_slug`` 一致）。

    含 ``\\`` / ``..`` / NUL / 空段 → None（拒绝，防路径逃逸）。
    """
    slug = skill_id.rsplit('/', 1)[-1].strip()
    if not slug or slug == '..' or '\\' in slug or '\x00' in slug or '..' in slug:
        return None
    return slug


async def _acquire_node_lock(lock_path: Path) -> bool:
    """O_CREAT|O_EXCL 抢节点锁；重试至 60s；mtime 超 10 分钟视为 stale 删除重抢。"""
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue  # 持有者刚释放，立即重试
            if age > LOCK_STALE_SECONDS:
                log.warning(f'[SharedSkills] 清理 stale 锁（{age:.0f}s > {LOCK_STALE_SECONDS}s）: {lock_path}')
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(_LOCK_POLL_SECONDS)
        else:
            os.write(fd, str(os.getpid()).encode('utf-8'))
            os.close(fd)
            return True


def _load_index(index_path: Path) -> dict[str, Any]:
    """读 .index.json；缺失/损坏 → 空账本（自愈：下一轮全量重物化）。"""
    if not index_path.exists():
        return {'revision': '', 'skills': {}}
    try:
        data = json.loads(index_path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('skills'), dict):
            raise ValueError('index 结构非法')
    except Exception as exc:
        log.warning(f'[SharedSkills] .index.json 损坏，按空账本重建: {exc}')
        return {'revision': '', 'skills': {}}
    return data


def _write_index_atomic(index_path: Path, index: dict[str, Any]) -> None:
    """临时文件 + os.replace 原子落盘，避免读到半写 index。"""
    tmp = index_path.with_name(f'{index_path.name}.tmp-{uuid.uuid4().hex[:8]}')
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    os.replace(tmp, index_path)


def _materialize_zip_sync(data: bytes, dest_dir: Path, staging_root: Path) -> None:
    """zip 字节 → 临时目录解压（带 zip-slip 防护）→ 整目录替换 dest_dir。"""
    staging = staging_root / f'.tmp-{dest_dir.name}-{uuid.uuid4().hex[:8]}'
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            for member in zf.namelist():
                member_path = Path(member)
                if member_path.is_absolute() or '..' in member_path.parts:
                    raise ValueError(f'zip 成员路径非法（疑似 zip-slip）: {member}')
            zf.extractall(staging)
        if not (staging / 'SKILL.md').exists():
            raise ValueError('包内缺 SKILL.md，拒绝物化')
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.move(str(staging), str(dest_dir))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


async def _fetch_common_skill_zip(db: AsyncSession, skill_id: str) -> bytes:
    """默认取包实现：与 open download 端点同源的两条路径。

    - 版本行有 package_url → httpx 直拉（reconciler 与市场同机，内网/CDN 拉包≈零成本）；
    - 否则 package_service.get_skill_package 本地打包（hub 仓同机）读字节。
    失败向上抛，由调用方计入失败清单（零 fake）。
    """
    version_row = await marketplace_skill_version_dao.get_latest_by_skill(db, skill_id)
    if version_row is not None and version_row.package_url:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(version_row.package_url)
            resp.raise_for_status()
            return resp.content
    package_path, _ = await package_service.get_skill_package(db, skill_id, None)
    return await run_in_threadpool(package_path.read_bytes)


async def reconcile_shared_common_skills(
    db: AsyncSession,
    shared_root: Path,
    *,
    fetch_zip: FetchZip | None = None,
) -> dict[str, Any]:
    """把云端公共技能 reconcile 进 ``{shared_root}/common/``（幂等，可反复调）。

    :param fetch_zip: 取 zip 字节的实现，默认 ``_fetch_common_skill_zip``；
        测试注入构造真实 zip 字节的实现（注入真实测试物料，非 mock 业务逻辑）。
    :return: ``{revision, materialized, kept, pruned, failed, bytes_downloaded, duration_ms}``
        统计（评审 O4 可观测性；failed 为 ``[{skill_id, error}]``，非空时 revision 保留旧值）。
    """
    fetch = fetch_zip or _fetch_common_skill_zip
    started = time.monotonic()

    common_dir = shared_root / 'common'
    skills_dir = common_dir / 'skills'
    skills_dir.mkdir(parents=True, exist_ok=True)
    lock_path = common_dir / '.lock'
    index_path = common_dir / '.index.json'

    if not await _acquire_node_lock(lock_path):
        raise TimeoutError(f'[SharedSkills] {LOCK_TIMEOUT_SECONDS}s 内未抢到共享目录锁: {lock_path}')

    materialized: list[str] = []
    kept: list[str] = []
    pruned: list[str] = []
    failed: list[dict[str, str]] = []
    bytes_downloaded = 0
    try:
        index = _load_index(index_path)
        index_skills: dict[str, Any] = index['skills']

        cloud_ids, cloud_revision = await get_common_skill_snapshot(db)
        fingerprints = await get_skills_content_fingerprints(db, cloud_ids)

        claimed_slugs: dict[str, str] = {}  # slug -> skill_id（防不同 skill_id 同 slug 互踩）
        for skill_id in cloud_ids:
            slug = _skill_slug(skill_id)
            if slug is None:
                failed.append({'skill_id': skill_id, 'error': 'invalid_slug'})
                log.error(f'[SharedSkills] slug 非法，跳过: {skill_id}')
                continue
            if slug in claimed_slugs:
                failed.append({'skill_id': skill_id, 'error': f'slug_conflict:{claimed_slugs[slug]}'})
                log.error(f'[SharedSkills] slug 冲突（{slug} 已被 {claimed_slugs[slug]} 占用），跳过: {skill_id}')
                continue
            claimed_slugs[slug] = skill_id

            fingerprint = fingerprints.get(skill_id, '')
            entry = index_skills.get(skill_id) or {}
            dest_dir = skills_dir / slug
            # 增量判据：指纹非空且一致 + 物料在场 → 跳过。指纹空（市场无版本行）诚实总是重下。
            if fingerprint and entry.get('fingerprint') == fingerprint and (dest_dir / 'SKILL.md').exists():
                kept.append(skill_id)
                continue

            try:
                data = await fetch(db, skill_id)
                await run_in_threadpool(_materialize_zip_sync, data, dest_dir, common_dir)
            except Exception as exc:
                failed.append({'skill_id': skill_id, 'error': str(exc)})
                log.error(f'[SharedSkills] 物化失败 {skill_id}: {exc}')
                continue
            bytes_downloaded += len(data)
            index_skills[skill_id] = {'slug': slug, 'fingerprint': fingerprint}
            materialized.append(skill_id)

        # 下架 prune（评审 D6）：只删 index 管理过的，云端清单外的手装目录不碰。
        cloud_id_set = set(cloud_ids)
        for stale_id in [sid for sid in index_skills if sid not in cloud_id_set]:
            stale_slug = str((index_skills.get(stale_id) or {}).get('slug') or '')
            stale_dir = skills_dir / stale_slug if stale_slug else None
            if stale_dir is not None and stale_dir.exists():
                await run_in_threadpool(shutil.rmtree, stale_dir)
            index_skills.pop(stale_id, None)
            pruned.append(stale_id)

        # 全部成功才推进 revision；部分失败保留旧值（下轮重试），成功条目已先记入。
        if not failed:
            index['revision'] = cloud_revision
        index['updated_at'] = datetime.now(dt_timezone.utc).isoformat()
        _write_index_atomic(index_path, index)
        written_revision = str(index.get('revision') or '')
    finally:
        lock_path.unlink(missing_ok=True)

    duration_ms = int((time.monotonic() - started) * 1000)
    stats = {
        'revision': written_revision,
        'materialized': materialized,
        'kept': kept,
        'pruned': pruned,
        'failed': failed,
        'bytes_downloaded': bytes_downloaded,
        'duration_ms': duration_ms,
    }
    log.info(
        f'[SharedSkills] reconcile 完成 root={shared_root} revision={written_revision} '
        f'物化={len(materialized)} 复用={len(kept)} 下架={len(pruned)} 失败={len(failed)} '
        f'下载={bytes_downloaded}B 耗时={duration_ms}ms'
        + (f' 失败清单={failed}' if failed else '')
    )
    return stats
