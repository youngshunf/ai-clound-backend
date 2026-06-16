"""配置/目录变更的 WS 主动推送与 revision 缓存（设计 doc02-数据与同步/07）。

职责：
  - 为 3 类全局配置/目录维护内容指纹 revision，缓存进 Redis（``hasn:sync:rev:{kind}``）。
  - bump：写点变更后重算 revision + 写缓存 + 经 ``ws_router`` 向在线节点 push
    ``hasn.sync.invalidate``（在线 daemon 秒级收到去拉最新，不再绑死登录/轮询）。
  - get_all_revisions：连接握手用，读缓存（miss 即重算并回填），cheap。

单 worker 部署（``deploy --workers 1`` / ``granian --workers 1``）：``ws_router._ws_connections``
是进程内全部连接，fan-out 直接遍历即完整覆盖，无需 Redis pub/sub 广播。离线节点不入离线队列——
invalidate 是幂等「去拉最新」信号，靠重连 ``hasn.connected`` 握手对账追平。

revision 范式对齐 ``common_skills_service`` / ``platform_default_config_service``：内容变 → 指纹变。
"""

from __future__ import annotations

import hashlib
import logging

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.database.redis import redis_client

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# revision 缓存键前缀
REV_PREFIX = 'hasn:sync:rev'
# 缓存 TTL（兜底：写点漏 bump 时，最长这么久后 get_all_revisions 缓存过期重算自愈）
REV_TTL_SECS = 3600

KIND_BUILTIN_CATALOG = 'builtin_catalog'
KIND_COMMON_SKILLS = 'common_skills'
KIND_PLATFORM_CONFIG = 'platform_config'
KINDS = (KIND_BUILTIN_CATALOG, KIND_COMMON_SKILLS, KIND_PLATFORM_CONFIG)

# 内置任务目录为空时的稳定指纹（对齐 common_skills 的 EMPTY 约定）
EMPTY_BUILTIN_CATALOG_REVISION = 'empty'


async def compute_builtin_catalog_revision(db: AsyncSession) -> str:
    """内置任务目录指纹：sha256(sorted "key@revision" 行)[:16]，对齐 common_skills_revision。

    catalog 任一行的 ``revision``（per-row 版本号）或成员增减 → 指纹变 → daemon 重拉。
    """
    from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog

    rows = (await db.execute(sa.select(HasnBuiltinTaskCatalog.builtin_key, HasnBuiltinTaskCatalog.revision))).all()
    lines = sorted(f'{key}@{rev}' for key, rev in rows if key)
    if not lines:
        return EMPTY_BUILTIN_CATALOG_REVISION
    signature = '\n'.join(lines)
    return hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]


async def _compute_revision(kind: str, db: AsyncSession) -> str:
    """按 kind 重算权威 revision（直接读各自数据源，不读缓存）。"""
    if kind == KIND_BUILTIN_CATALOG:
        return await compute_builtin_catalog_revision(db)
    if kind == KIND_COMMON_SKILLS:
        from backend.app.marketplace.service.common_skills_service import (
            get_common_skill_snapshot,
        )

        _, rev = await get_common_skill_snapshot(db)
        return rev
    if kind == KIND_PLATFORM_CONFIG:
        from backend.app.hasn.service.platform_default_config_service import (
            platform_default_config_service,
        )

        _, rev = await platform_default_config_service.get_effective_config(db)
        return rev
    raise ValueError(f'unknown sync kind: {kind}')


async def get_all_revisions(db: AsyncSession) -> dict[str, str]:
    """连接握手用：返回 ``{kind: revision}``。读 Redis 缓存，miss 即重算并回填（cheap）。

    redis 不可用 → 退化为每次重算（仍返回正确 revision，只是不省那几次查询）。
    """
    out: dict[str, str] = {}
    for kind in KINDS:
        cached = None
        try:
            cached = await redis_client.get(f'{REV_PREFIX}:{kind}')
        except Exception as exc:  # redis 故障退化为重算
            logger.warning('[sync] read revision cache failed kind=%s: %s', kind, exc)
        if cached:
            out[kind] = cached
            continue
        rev = await _compute_revision(kind, db)
        out[kind] = rev
        try:
            await redis_client.set(f'{REV_PREFIX}:{kind}', rev, ex=REV_TTL_SECS)
        except Exception as exc:
            logger.warning('[sync] write revision cache failed kind=%s: %s', kind, exc)
    return out


async def bump(kind: str, db: AsyncSession, *, owner_id: str | None = None) -> str:
    """写点变更后调用：重算 revision → 写缓存 → push ``hasn.sync.invalidate`` 给在线节点。

    - ``owner_id=None`` → 全局广播（全部在线节点）；指定 → 仅推该 owner 的在线节点。
    - 返回新 revision。push 失败不抛（best-effort）：离线节点靠重连握手对账追平，
      写点（如 admin PUT）绝不能因推送失败而失败。
    """
    if kind not in KINDS:
        raise ValueError(f'unknown sync kind: {kind}')
    rev = await _compute_revision(kind, db)
    try:
        await redis_client.set(f'{REV_PREFIX}:{kind}', rev, ex=REV_TTL_SECS)
    except Exception as exc:
        logger.warning('[sync] cache revision failed kind=%s: %s', kind, exc)

    from backend.app.hasn.service.ws_router import ws_router

    try:
        pushed = await ws_router.broadcast_sync_invalidate(kind, rev, owner_id=owner_id)
        logger.info(
            '[sync] invalidate kind=%s rev=%s pushed=%d owner=%s',
            kind,
            rev,
            pushed,
            owner_id or '*',
        )
    except Exception as exc:  # 推送 best-effort，不拖垮写点
        logger.warning('[sync] broadcast invalidate failed kind=%s: %s', kind, exc)
    return rev
