"""技能市场管理员同步 API。"""
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from backend.app.marketplace.model import MarketplaceSyncLog
from backend.app.marketplace.service.clawhub_sync_service import clawhub_sync_service
from backend.app.marketplace.service.github_app_sync_service import github_app_sync_service
from backend.app.marketplace.service.package_service import package_service
from backend.app.marketplace.service.skill_translation_backfill_service import backfill_skill_translations
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession

router = APIRouter(dependencies=[DependsRBAC])


class SyncRequest(BaseModel):
    """同步请求。"""

    force: bool = False
    skill_ids: list[str] | None = None
    # ClawHub 同步专用：
    limit: int | None = None  # top-N 上限（None=用配置默认；0=不截断）
    min_downloads: int | None = None  # 下载量阈值：只同步 downloads 严格大于该值（None=用配置默认）
    dry_run: bool = False  # 只评估不落库：返回命中数量+占用预估


class RetranslateRequest(BaseModel):
    """翻译回填请求。"""

    only_missing: bool = False
    skill_ids: list[str] | None = None
    limit: int | None = None
    batch_size: int = 10
    concurrency: int = 4


@router.post('/github', summary='已退役：服务器 GitHub 技能同步')
async def trigger_github_sync(
    _db: CurrentSession,
    _request: SyncRequest,
) -> dict[str, Any]:
    """明确拒绝服务器克隆和打包技能的旧入口。"""
    raise HTTPException(
        status_code=410,
        detail=(
            'GitHub 技能服务器同步已退役，请在可信 huanxing-hub 工作区运行 '
            'scripts/publish_skills.py'
        ),
    )


@router.post('/github/templates', summary='Trigger GitHub template sync')
async def trigger_github_template_sync(
    db: CurrentSession,
    request: SyncRequest
) -> dict[str, Any]:
    """Trigger template sync from GitHub repository"""
    result = await github_app_sync_service.sync_from_github(
        db=db,
        force=request.force
    )
    return result


@router.post('/clawhub', summary='Trigger ClawHub sync')
async def trigger_clawhub_sync(
    db: CurrentSession,
    request: SyncRequest
) -> dict[str, Any]:
    """Trigger sync from ClawHub marketplace"""
    result = await clawhub_sync_service.sync_from_clawhub(
        db=db,
        force=request.force,
        skill_ids=request.skill_ids,
        limit=request.limit,
        min_downloads=request.min_downloads,
        dry_run=request.dry_run
    )
    return result


@router.post('/retranslate', summary='Backfill bilingual translations + emoji')
async def trigger_retranslate(
    db: CurrentSession,
    request: RetranslateRequest
) -> dict[str, Any]:
    """Re-translate existing skills (name/description/tags + emoji) in batches."""
    return await backfill_skill_translations(
        db,
        only_missing=request.only_missing,
        skill_ids=request.skill_ids,
        limit=request.limit,
        batch_size=request.batch_size,
        concurrency=request.concurrency,
    )


@router.get('/status', summary='Get sync status')
async def get_sync_status(db: CurrentSession) -> dict[str, Any]:
    """返回来源链路状态和最近一次真实 ClawHub 同步日志。"""
    result = await db.execute(
        select(MarketplaceSyncLog)
        .where(MarketplaceSyncLog.sync_type == 'clawhub')
        .order_by(MarketplaceSyncLog.started_at.desc())
        .limit(1)
    )
    clawhub_log = result.scalar_one_or_none()
    return {
        'github': {
            'status': 'retired',
            'distribution': 'source_release',
            'publisher': 'scripts/publish_skills.py',
        },
        'clawhub': {
            'status': clawhub_log.status if clawhub_log else 'never_synced',
            'last_sync': (
                (clawhub_log.completed_at or clawhub_log.started_at).isoformat()
                if clawhub_log
                else None
            ),
            'items_synced': clawhub_log.items_synced if clawhub_log else None,
            'items_failed': clawhub_log.items_failed if clawhub_log else None,
            'error_message': clawhub_log.error_message if clawhub_log else None,
        },
    }


@router.delete('/cache', summary='Clear package cache')
async def clear_package_cache(
    skill_id: Annotated[str | None, Query(description='Skill ID (clear all if not specified)')] = None
) -> dict[str, Any]:
    """Clear package cache"""
    await package_service.clear_cache(skill_id)
    return {'success': True, 'message': 'Cache cleared'}


@router.get('/cache/stats', summary='Get cache statistics')
async def get_cache_stats() -> dict[str, Any]:
    """Get package cache statistics"""
    stats = await package_service.get_cache_stats()
    return stats
