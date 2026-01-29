"""技能市场下载 API

提供技能和应用的下载功能，返回预签名 URL
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel

from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.crud.crud_marketplace_app import marketplace_app_dao
from backend.app.marketplace.crud.crud_marketplace_app_version import marketplace_app_version_dao
from backend.app.marketplace.crud.crud_marketplace_download import marketplace_download_dao
from backend.app.marketplace.schema.marketplace_download import CreateMarketplaceDownloadParam
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class DownloadResponse(BaseModel):
    """下载响应"""
    download_url: str
    file_hash: str | None
    file_size: int | None


class AppDownloadResponse(BaseModel):
    """应用下载响应，包含依赖技能信息"""
    download_url: str
    file_hash: str | None
    file_size: int | None
    skill_dependencies: list[dict] | None  # [{id, version, download_url}]


@router.get(
    '/skill/{skill_id}/{version}',
    summary='下载技能包',
    description='获取技能包的下载链接，同时记录下载历史',
)
async def download_skill(
    db: CurrentSession,
    skill_id: Annotated[str, Path(description='技能ID')],
    version: Annotated[str, Path(description='版本号，可以是具体版本或 latest')],
) -> ResponseSchemaModel[DownloadResponse]:
    # 获取技能
    skill = await marketplace_skill_dao.get_by_id(db, skill_id)
    if not skill:
        raise errors.NotFoundError(msg='技能不存在')
    
    # 获取版本
    if version == 'latest':
        skill_version = await marketplace_skill_version_dao.get_latest_by_skill(db, skill_id)
    else:
        skill_version = await marketplace_skill_version_dao.get_by_skill_and_version(db, skill_id, version)
    
    if not skill_version:
        raise errors.NotFoundError(msg='版本不存在')
    
    if not skill_version.package_url:
        raise errors.NotFoundError(msg='包文件不存在')
    
    return response_base.success(data=DownloadResponse(
        download_url=skill_version.package_url,
        file_hash=skill_version.file_hash,
        file_size=skill_version.file_size,
    ))


@router.post(
    '/skill/{skill_id}/{version}',
    summary='下载技能包并记录',
    description='获取技能包的下载链接，需要登录，记录下载历史',
    dependencies=[DependsJwtAuth],
)
async def download_skill_with_record(
    db: CurrentSessionTransaction,
    request: Request,
    skill_id: Annotated[str, Path(description='技能ID')],
    version: Annotated[str, Path(description='版本号，可以是具体版本或 latest')],
) -> ResponseSchemaModel[DownloadResponse]:
    # 获取技能
    skill = await marketplace_skill_dao.get_by_id(db, skill_id)
    if not skill:
        raise errors.NotFoundError(msg='技能不存在')
    
    # 获取版本
    if version == 'latest':
        skill_version = await marketplace_skill_version_dao.get_latest_by_skill(db, skill_id)
    else:
        skill_version = await marketplace_skill_version_dao.get_by_skill_and_version(db, skill_id, version)
    
    if not skill_version:
        raise errors.NotFoundError(msg='版本不存在')
    
    if not skill_version.package_url:
        raise errors.NotFoundError(msg='包文件不存在')
    
    # 记录下载历史
    await marketplace_download_dao.create(db, CreateMarketplaceDownloadParam(
        user_id=request.user.id,
        item_type='skill',
        item_id=skill_id,
        version=skill_version.version,
    ))
    
    # 更新下载计数
    await marketplace_skill_dao.increment_download_count(db, skill_id)
    
    return response_base.success(data=DownloadResponse(
        download_url=skill_version.package_url,
        file_hash=skill_version.file_hash,
        file_size=skill_version.file_size,
    ))


@router.get(
    '/app/{app_id}/{version}',
    summary='下载应用包',
    description='获取应用包的下载链接，包含依赖技能信息',
)
async def download_app(
    db: CurrentSession,
    app_id: Annotated[str, Path(description='应用ID')],
    version: Annotated[str, Path(description='版本号，可以是具体版本或 latest')],
) -> ResponseSchemaModel[AppDownloadResponse]:
    # 获取应用
    app = await marketplace_app_dao.get_by_id(db, app_id)
    if not app:
        raise errors.NotFoundError(msg='应用不存在')
    
    # 获取版本
    if version == 'latest':
        app_version = await marketplace_app_version_dao.get_latest_by_app(db, app_id)
    else:
        app_version = await marketplace_app_version_dao.get_by_app_and_version(db, app_id, version)
    
    if not app_version:
        raise errors.NotFoundError(msg='版本不存在')
    
    if not app_version.package_url:
        raise errors.NotFoundError(msg='包文件不存在')
    
    # 解析技能依赖
    skill_dependencies = []
    if app_version.skill_dependencies_versioned:
        for skill_id, ver_spec in app_version.skill_dependencies_versioned.items():
            # 获取依赖技能的版本信息
            skill_ver = await marketplace_skill_version_dao.get_latest_by_skill(db, skill_id)
            if skill_ver and skill_ver.package_url:
                skill_dependencies.append({
                    'id': skill_id,
                    'version': skill_ver.version,
                    'download_url': skill_ver.package_url,
                    'file_hash': skill_ver.file_hash,
                })
    
    return response_base.success(data=AppDownloadResponse(
        download_url=app_version.package_url,
        file_hash=app_version.file_hash,
        file_size=app_version.file_size,
        skill_dependencies=skill_dependencies if skill_dependencies else None,
    ))


@router.post(
    '/app/{app_id}/{version}',
    summary='下载应用包并记录',
    description='获取应用包的下载链接，需要登录，记录下载历史',
    dependencies=[DependsJwtAuth],
)
async def download_app_with_record(
    db: CurrentSessionTransaction,
    request: Request,
    app_id: Annotated[str, Path(description='应用ID')],
    version: Annotated[str, Path(description='版本号，可以是具体版本或 latest')],
) -> ResponseSchemaModel[AppDownloadResponse]:
    # 获取应用
    app = await marketplace_app_dao.get_by_id(db, app_id)
    if not app:
        raise errors.NotFoundError(msg='应用不存在')
    
    # 获取版本
    if version == 'latest':
        app_version = await marketplace_app_version_dao.get_latest_by_app(db, app_id)
    else:
        app_version = await marketplace_app_version_dao.get_by_app_and_version(db, app_id, version)
    
    if not app_version:
        raise errors.NotFoundError(msg='版本不存在')
    
    if not app_version.package_url:
        raise errors.NotFoundError(msg='包文件不存在')
    
    # 记录下载历史
    await marketplace_download_dao.create(db, CreateMarketplaceDownloadParam(
        user_id=request.user.id,
        item_type='app',
        item_id=app_id,
        version=app_version.version,
    ))
    
    # 更新下载计数
    await marketplace_app_dao.increment_download_count(db, app_id)
    
    # 解析技能依赖
    skill_dependencies = []
    if app_version.skill_dependencies_versioned:
        for skill_id, ver_spec in app_version.skill_dependencies_versioned.items():
            skill_ver = await marketplace_skill_version_dao.get_latest_by_skill(db, skill_id)
            if skill_ver and skill_ver.package_url:
                skill_dependencies.append({
                    'id': skill_id,
                    'version': skill_ver.version,
                    'download_url': skill_ver.package_url,
                    'file_hash': skill_ver.file_hash,
                })
    
    return response_base.success(data=AppDownloadResponse(
        download_url=app_version.package_url,
        file_hash=app_version.file_hash,
        file_size=app_version.file_size,
        skill_dependencies=skill_dependencies if skill_dependencies else None,
    ))
