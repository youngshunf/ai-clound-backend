"""技能/应用发布 API

提供给 CLI 工具远程发布使用的接口。
需要 API Key 认证。
"""
import hashlib
import tempfile
import zipfile
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Optional

import yaml
import json
from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model import (
    MarketplaceSkill,
    MarketplaceSkillVersion,
    MarketplaceApp,
    MarketplaceAppVersion,
)
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


# ============================================================
# API Key 认证
# ============================================================

async def verify_publish_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """验证发布 API Key"""
    if not x_api_key:
        raise errors.AuthorizationError(msg='缺少 API Key')
    
    # TODO: 从数据库或配置中验证 API Key
    # 目前使用简单的环境变量验证
    import os
    valid_key = os.environ.get('MARKETPLACE_PUBLISH_API_KEY', 'dev-publish-key')
    if x_api_key != valid_key:
        raise errors.AuthorizationError(msg='无效的 API Key')
    
    return x_api_key


# ============================================================
# 响应模型
# ============================================================

class PublishResult(BaseModel):
    """发布结果"""
    id: str
    version: str
    package_url: str
    file_hash: str
    file_size: int


# ============================================================
# 技能发布 API
# ============================================================

@router.post('/skill', summary='发布技能包', dependencies=[Depends(verify_publish_api_key)])
async def publish_skill(
    db: CurrentSession,
    file: Annotated[UploadFile, File(description='技能包 ZIP 文件')],
    version: Annotated[str | None, Form(description='版本号，不指定则使用包内版本')] = None,
    changelog: Annotated[str | None, Form(description='更新日志')] = None,
) -> ResponseSchemaModel[PublishResult]:
    """
    发布技能包
    
    上传 ZIP 格式的技能包，包含：
    - config.yaml (必需)
    - SKILL.md (必需)
    - icon.svg (可选)
    """
    # 读取上传的文件
    content = await file.read()
    
    # 解析技能包
    try:
        with zipfile.ZipFile(BytesIO(content), 'r') as zf:
            # 读取 config.yaml
            if 'config.yaml' not in zf.namelist():
                raise errors.RequestError(msg='技能包缺少 config.yaml')
            
            config_content = zf.read('config.yaml').decode('utf-8')
            config = yaml.safe_load(config_content)
            
            # 验证必需字段
            required_fields = ['id', 'name', 'version', 'description']
            for field in required_fields:
                if field not in config:
                    raise errors.RequestError(msg=f'config.yaml 缺少必需字段: {field}')
            
            # 读取图标（可选）
            icon_content = None
            if 'icon.svg' in zf.namelist():
                icon_content = zf.read('icon.svg')
    except zipfile.BadZipFile:
        raise errors.RequestError(msg='无效的 ZIP 文件')
    
    skill_id = config['id']
    final_version = version or config['version']
    
    # 计算哈希
    file_hash = hashlib.sha256(content).hexdigest()
    file_size = len(content)
    
    # 上传到 S3
    package_url, _, _ = await marketplace_storage_service.upload_skill_package(
        db=db,
        skill_id=skill_id,
        version=final_version,
        content=content,
    )
    
    # 上传图标
    icon_url = None
    if icon_content:
        icon_url = await marketplace_storage_service.upload_icon(
            db=db,
            item_type='skill',
            item_id=skill_id,
            content=icon_content,
        )
    
    # 更新数据库
    await _save_skill_to_db(
        db=db,
        skill_id=skill_id,
        config=config,
        version=final_version,
        changelog=changelog,
        package_url=package_url,
        file_hash=file_hash,
        file_size=file_size,
        icon_url=icon_url,
    )
    
    return response_base.success(data=PublishResult(
        id=skill_id,
        version=final_version,
        package_url=package_url,
        file_hash=file_hash,
        file_size=file_size,
    ))


async def _save_skill_to_db(
    db: AsyncSession,
    skill_id: str,
    config: dict,
    version: str,
    changelog: str | None,
    package_url: str,
    file_hash: str,
    file_size: int,
    icon_url: str | None,
) -> None:
    """保存技能到数据库"""
    # 检查技能是否存在
    stmt = select(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id)
    result = await db.execute(stmt)
    skill = result.scalar_one_or_none()
    
    if not skill:
        # 创建新技能
        skill = MarketplaceSkill(
            skill_id=skill_id,
            name=config['name'],
            description=config['description'],
            icon_url=icon_url,
            author_name=config.get('author_name', ''),
            pricing_type=config.get('pricing_type', 'free'),
            price=Decimal('0'),
            tags=','.join(config.get('tags', [])) if config.get('tags') else None,
            category=config.get('category'),
            is_private=False,
            is_official=False,
            download_count=0,
        )
        db.add(skill)
        await db.flush()
    else:
        # 更新技能
        update_data = {
            'name': config['name'],
            'description': config['description'],
            'pricing_type': config.get('pricing_type', 'free'),
            'tags': ','.join(config.get('tags', [])) if config.get('tags') else None,
            'category': config.get('category'),
        }
        if icon_url:
            update_data['icon_url'] = icon_url
        
        stmt = update(MarketplaceSkill).where(
            MarketplaceSkill.skill_id == skill_id
        ).values(**update_data)
        await db.execute(stmt)
    
    # 清除旧版本的 is_latest 标志
    stmt = update(MarketplaceSkillVersion).where(
        MarketplaceSkillVersion.skill_id == skill_id,
        MarketplaceSkillVersion.is_latest == True,
    ).values(is_latest=False)
    await db.execute(stmt)
    
    # 检查版本是否存在
    stmt = select(MarketplaceSkillVersion).where(
        MarketplaceSkillVersion.skill_id == skill_id,
        MarketplaceSkillVersion.version == version,
    )
    result = await db.execute(stmt)
    existing_version = result.scalar_one_or_none()
    
    if existing_version:
        # 更新版本
        stmt = update(MarketplaceSkillVersion).where(
            MarketplaceSkillVersion.skill_id == skill_id,
            MarketplaceSkillVersion.version == version,
        ).values(
            changelog=changelog,
            package_url=package_url,
            file_hash=file_hash,
            file_size=file_size,
            is_latest=True,
        )
        await db.execute(stmt)
    else:
        # 创建版本
        skill_version = MarketplaceSkillVersion(
            skill_id=skill_id,
            version=version,
            changelog=changelog,
            package_url=package_url,
            file_hash=file_hash,
            file_size=file_size,
            is_latest=True,
        )
        db.add(skill_version)
    
    await db.commit()


# ============================================================
# 应用发布 API
# ============================================================

@router.post('/app', summary='发布应用包', dependencies=[Depends(verify_publish_api_key)])
async def publish_app(
    db: CurrentSession,
    file: Annotated[UploadFile, File(description='应用包 ZIP 文件')],
    version: Annotated[str | None, Form(description='版本号，不指定则使用包内版本')] = None,
    changelog: Annotated[str | None, Form(description='更新日志')] = None,
) -> ResponseSchemaModel[PublishResult]:
    """
    发布应用包
    
    上传 ZIP 格式的应用包，包含：
    - manifest.json (必需)
    - assets/icon.svg (可选)
    """
    # 读取上传的文件
    content = await file.read()
    
    # 解析应用包
    try:
        with zipfile.ZipFile(BytesIO(content), 'r') as zf:
            # 读取 manifest.json
            if 'manifest.json' not in zf.namelist():
                raise errors.RequestError(msg='应用包缺少 manifest.json')
            
            manifest_content = zf.read('manifest.json').decode('utf-8')
            manifest = json.loads(manifest_content)
            
            # 验证必需字段
            required_fields = ['id', 'name', 'version', 'description']
            for field in required_fields:
                if field not in manifest:
                    raise errors.RequestError(msg=f'manifest.json 缺少必需字段: {field}')
            
            # 读取图标（可选）
            icon_content = None
            if 'assets/icon.svg' in zf.namelist():
                icon_content = zf.read('assets/icon.svg')
    except zipfile.BadZipFile:
        raise errors.RequestError(msg='无效的 ZIP 文件')
    except json.JSONDecodeError:
        raise errors.RequestError(msg='manifest.json 格式错误')
    
    app_id = manifest['id']
    final_version = version or manifest['version']
    
    # 计算哈希
    file_hash = hashlib.sha256(content).hexdigest()
    file_size = len(content)
    
    # 上传到 S3
    package_url, _, _ = await marketplace_storage_service.upload_app_package(
        db=db,
        app_id=app_id,
        version=final_version,
        content=content,
    )
    
    # 上传图标
    icon_url = None
    if icon_content:
        icon_url = await marketplace_storage_service.upload_icon(
            db=db,
            item_type='app',
            item_id=app_id,
            content=icon_content,
        )
    
    # 更新数据库
    await _save_app_to_db(
        db=db,
        app_id=app_id,
        manifest=manifest,
        version=final_version,
        changelog=changelog,
        package_url=package_url,
        file_hash=file_hash,
        file_size=file_size,
        icon_url=icon_url,
    )
    
    return response_base.success(data=PublishResult(
        id=app_id,
        version=final_version,
        package_url=package_url,
        file_hash=file_hash,
        file_size=file_size,
    ))


async def _save_app_to_db(
    db: AsyncSession,
    app_id: str,
    manifest: dict,
    version: str,
    changelog: str | None,
    package_url: str,
    file_hash: str,
    file_size: int,
    icon_url: str | None,
) -> None:
    """保存应用到数据库"""
    skill_dependencies = manifest.get('skill_dependencies', [])
    skill_deps_str = ','.join(skill_dependencies) if skill_dependencies else None
    
    # 检查应用是否存在
    stmt = select(MarketplaceApp).where(MarketplaceApp.app_id == app_id)
    result = await db.execute(stmt)
    app = result.scalar_one_or_none()
    
    if not app:
        # 创建新应用
        app = MarketplaceApp(
            app_id=app_id,
            name=manifest['name'],
            description=manifest['description'],
            icon_url=icon_url,
            author_name=manifest.get('author_name', ''),
            pricing_type=manifest.get('pricing_type', 'free'),
            price=Decimal('0'),
            skill_dependencies=skill_deps_str,
            is_private=False,
            is_official=False,
            download_count=0,
        )
        db.add(app)
        await db.flush()
    else:
        # 更新应用
        update_data = {
            'name': manifest['name'],
            'description': manifest['description'],
            'pricing_type': manifest.get('pricing_type', 'free'),
            'skill_dependencies': skill_deps_str,
        }
        if icon_url:
            update_data['icon_url'] = icon_url
        
        stmt = update(MarketplaceApp).where(
            MarketplaceApp.app_id == app_id
        ).values(**update_data)
        await db.execute(stmt)
    
    # 清除旧版本的 is_latest 标志
    stmt = update(MarketplaceAppVersion).where(
        MarketplaceAppVersion.app_id == app_id,
        MarketplaceAppVersion.is_latest == True,
    ).values(is_latest=False)
    await db.execute(stmt)
    
    # 版本化的技能依赖
    skill_deps_versioned = {}
    for dep in skill_dependencies:
        if '@' in dep:
            sid, ver = dep.split('@', 1)
            skill_deps_versioned[sid] = ver
        else:
            skill_deps_versioned[dep] = '*'
    
    # 检查版本是否存在
    stmt = select(MarketplaceAppVersion).where(
        MarketplaceAppVersion.app_id == app_id,
        MarketplaceAppVersion.version == version,
    )
    result = await db.execute(stmt)
    existing_version = result.scalar_one_or_none()
    
    if existing_version:
        # 更新版本
        stmt = update(MarketplaceAppVersion).where(
            MarketplaceAppVersion.app_id == app_id,
            MarketplaceAppVersion.version == version,
        ).values(
            changelog=changelog,
            skill_dependencies_versioned=skill_deps_versioned if skill_deps_versioned else None,
            package_url=package_url,
            file_hash=file_hash,
            file_size=file_size,
            is_latest=True,
        )
        await db.execute(stmt)
    else:
        # 创建版本
        app_version = MarketplaceAppVersion(
            app_id=app_id,
            version=version,
            changelog=changelog,
            skill_dependencies_versioned=skill_deps_versioned if skill_deps_versioned else None,
            package_url=package_url,
            file_hash=file_hash,
            file_size=file_size,
            is_latest=True,
        )
        db.add(app_version)
    
    await db.commit()
