"""桌面端版本检测公开 API

提供给桌面应用使用的版本检测接口，不需要登录认证。
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from backend.app.admin.crud.crud_app_version import app_version_dao
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


# ============================================================
# Schema 定义
# ============================================================

class BinaryInfo(BaseModel):
    """二进制文件信息"""
    url: str = Field(description='下载地址')
    sha256: str = Field(description='SHA256 校验值')
    size: int = Field(description='文件大小（字节）')
    filename: Optional[str] = Field(None, description='文件名')


class VersionManifest(BaseModel):
    """版本清单（兼容桌面端现有格式）"""
    version: str = Field(description='版本号')
    build_time: Optional[str] = Field(None, description='构建时间')
    build_timestamp: Optional[int] = Field(None, description='构建时间戳')
    changelog: Optional[str] = Field(None, description='更新日志')
    is_force_update: bool = Field(False, description='是否强制更新')
    min_version: Optional[str] = Field(None, description='最小兼容版本')
    binaries: dict[str, BinaryInfo] = Field(default_factory=dict, description='各平台二进制文件信息')


class VersionCheckResponse(BaseModel):
    """版本检测响应"""
    latest_version: Optional[str] = Field(None, description='最新版本号')
    has_update: bool = Field(False, description='是否有更新')
    is_force_update: bool = Field(False, description='是否强制更新')
    changelog: Optional[str] = Field(None, description='更新日志')
    download_url: Optional[str] = Field(None, description='下载地址')
    file_hash: Optional[str] = Field(None, description='SHA256 校验值')
    file_size: Optional[int] = Field(None, description='文件大小')


# ============================================================
# 版本检测 API
# ============================================================

@router.get('/latest', summary='获取最新版本号')
async def get_latest_version(
    db: CurrentSession,
    app_code: str = Query('creator-flow', description='应用标识'),
    platform: str = Query(..., description='平台: darwin/win32/linux'),
    arch: str = Query(..., description='架构: x64/arm64'),
) -> ResponseSchemaModel[Optional[str]]:
    """
    获取指定应用、平台、架构的最新版本号
    
    返回最新发布版本的版本号，如果没有发布版本则返回 null
    """
    latest = await app_version_dao.get_latest_published(
        db=db,
        app_code=app_code,
        platform=platform,
        arch=arch,
    )
    return response_base.success(data=latest.version if latest else None)


@router.get('/check', summary='检测版本更新')
async def check_version(
    db: CurrentSession,
    current_version: str = Query(..., description='当前版本号'),
    app_code: str = Query('creator-flow', description='应用标识'),
    platform: str = Query(..., description='平台: darwin/win32/linux'),
    arch: str = Query(..., description='架构: x64/arm64'),
) -> ResponseSchemaModel[VersionCheckResponse]:
    """
    检测是否有新版本可用
    
    比较当前版本与最新发布版本，返回更新信息
    """
    latest = await app_version_dao.get_latest_published(
        db=db,
        app_code=app_code,
        platform=platform,
        arch=arch,
    )
    
    if not latest:
        return response_base.success(data=VersionCheckResponse(
            has_update=False,
        ))
    
    # 简单的版本比较
    has_update = _is_newer_version(latest.version, current_version)
    
    return response_base.success(data=VersionCheckResponse(
        latest_version=latest.version,
        has_update=has_update,
        is_force_update=latest.is_force_update if has_update else False,
        changelog=latest.changelog if has_update else None,
        download_url=latest.download_url if has_update else None,
        file_hash=latest.file_hash if has_update else None,
        file_size=latest.file_size if has_update else None,
    ))


@router.get('/manifest/{version}', summary='获取版本清单')
async def get_version_manifest(
    db: CurrentSession,
    version: Annotated[str, Path(description='版本号')],
    app_code: str = Query('creator-flow', description='应用标识'),
) -> ResponseSchemaModel[Optional[VersionManifest]]:
    """
    获取指定版本的完整清单（包含所有平台的二进制文件信息）
    
    返回格式兼容桌面端现有的 VersionManifest 接口
    """
    versions = await app_version_dao.get_manifest(
        db=db,
        app_code=app_code,
        version=version,
    )
    
    if not versions:
        return response_base.success(data=None)
    
    # 构建 binaries 字典
    binaries: dict[str, BinaryInfo] = {}
    changelog = None
    is_force_update = False
    min_version = None
    build_time = None
    build_timestamp = None
    
    for v in versions:
        # 平台键格式：darwin-arm64, win32-x64 等
        platform_key = f'{v.platform}-{v.arch}'
        binaries[platform_key] = BinaryInfo(
            url=v.download_url,
            sha256=v.file_hash,
            size=v.file_size,
            filename=v.filename,
        )
        # 取第一个版本的元数据
        if changelog is None:
            changelog = v.changelog
            is_force_update = v.is_force_update
            min_version = v.min_version
            if v.published_at:
                build_time = v.published_at.isoformat()
                build_timestamp = int(v.published_at.timestamp())
    
    manifest = VersionManifest(
        version=version,
        build_time=build_time,
        build_timestamp=build_timestamp,
        changelog=changelog,
        is_force_update=is_force_update,
        min_version=min_version,
        binaries=binaries,
    )
    
    return response_base.success(data=manifest)


# ============================================================
# electron-updater 兼容的 YAML 接口
# electron-updater 会请求 latest-mac.yml / latest.yml / latest-linux.yml
# ============================================================

from fastapi.responses import PlainTextResponse
import yaml


@router.get('/latest-mac.yml', summary='macOS 更新清单 (electron-updater)')
async def get_latest_mac_yml(
    db: CurrentSession,
    app_code: str = Query('creator-flow', description='应用标识'),
) -> PlainTextResponse:
    """
    返回 electron-updater 需要的 latest-mac.yml 格式
    支持 arm64 和 x64 架构
    """
    return await _build_electron_updater_yml(db, app_code, 'darwin')


@router.get('/latest.yml', summary='Windows 更新清单 (electron-updater)')
async def get_latest_win_yml(
    db: CurrentSession,
    app_code: str = Query('creator-flow', description='应用标识'),
) -> PlainTextResponse:
    """
    返回 electron-updater 需要的 latest.yml 格式 (Windows)
    """
    return await _build_electron_updater_yml(db, app_code, 'win32')


@router.get('/latest-linux.yml', summary='Linux 更新清单 (electron-updater)')
async def get_latest_linux_yml(
    db: CurrentSession,
    app_code: str = Query('creator-flow', description='应用标识'),
) -> PlainTextResponse:
    """
    返回 electron-updater 需要的 latest-linux.yml 格式
    """
    return await _build_electron_updater_yml(db, app_code, 'linux')


async def _build_electron_updater_yml(
    db: CurrentSession,
    app_code: str,
    platform: str,
) -> PlainTextResponse:
    """
    构建 electron-updater 需要的 YAML 格式
    
    格式示例:
    version: 1.0.0
    files:
      - url: CreatorFlow-arm64.zip
        sha512: abc123...
        size: 12345678
    path: CreatorFlow-arm64.zip
    sha512: abc123...
    releaseDate: '2024-01-01T00:00:00.000Z'
    """
    from sqlalchemy import select, desc
    from backend.app.admin.model import AppVersion
    
    # 查找该平台的最新发布版本（所有架构）
    stmt = (
        select(AppVersion)
        .where(
            AppVersion.app_code == app_code,
            AppVersion.platform == platform,
            AppVersion.status == 'published',
        )
        .order_by(desc(AppVersion.published_at))
    )
    result = await db.execute(stmt)
    versions = result.scalars().all()
    
    if not versions:
        # 没有发布版本，返回 404
        return PlainTextResponse(
            content='# No published version available',
            status_code=404,
            media_type='text/yaml',
        )
    
    # 按版本号分组，取最新版本
    latest_version = versions[0].version
    latest_files = [v for v in versions if v.version == latest_version]
    
    if not latest_files:
        return PlainTextResponse(
            content='# No files found for latest version',
            status_code=404,
            media_type='text/yaml',
        )
    
    # 构建 files 列表
    files = []
    first_file = latest_files[0]
    
    for v in latest_files:
        file_entry = {
            'url': v.download_url,
            'sha512': v.file_hash,  # electron-updater 使用 sha512，我们存的是 sha256，但字段名仍用 sha512
            'size': v.file_size,
        }
        files.append(file_entry)
    
    # 构建 YAML 内容
    yml_data = {
        'version': latest_version,
        'files': files,
        'path': first_file.download_url,
        'sha512': first_file.file_hash,
        'releaseDate': first_file.published_at.isoformat() if first_file.published_at else None,
    }
    
    # 添加可选字段
    if first_file.changelog:
        yml_data['releaseNotes'] = first_file.changelog
    
    yml_content = yaml.dump(yml_data, allow_unicode=True, default_flow_style=False)
    
    return PlainTextResponse(
        content=yml_content,
        media_type='text/yaml',
    )


# ============================================================
# 辅助函数
# ============================================================

def _is_newer_version(latest: str, current: str) -> bool:
    """简单的语义化版本比较"""
    try:
        def parse_version(v: str) -> tuple[int, ...]:
            v = v.lstrip('v')
            # 移除预发布标识 (如 -beta.1)
            v = v.split('-')[0]
            return tuple(int(x) for x in v.split('.'))
        
        latest_parts = parse_version(latest)
        current_parts = parse_version(current)
        return latest_parts > current_parts
    except (ValueError, AttributeError):
        return False
