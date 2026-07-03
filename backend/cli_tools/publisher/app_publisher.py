"""应用发布服务

处理应用的验证、打包、上传和数据库记录创建。
"""

import json

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model import MarketplaceTemplate, MarketplaceTemplateVersion
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.cli_tools.cli.common import (
    VersionInfo,
    format_size,
    print_error,
    print_info,
    print_success,
)
from backend.cli_tools.packager.app_packager import AppPackager
from backend.cli_tools.validator.app_validator import AppValidator


@dataclass
class PublishResult:
    """发布结果"""
    success: bool
    app_id: str = ''
    version: str = ''
    package_url: str = ''
    file_hash: str = ''
    file_size: int = 0
    error: str = ''


class AppPublisher:
    """应用发布服务"""

    def __init__(self, app_path: Path) -> None:
        self.app_path = Path(app_path).resolve()
        self.validator = AppValidator(app_path)
        self.packager = AppPackager(app_path)

    async def publish(
        self,
        db: AsyncSession,
        bump: Literal['patch', 'minor', 'major'] | None = None,
        version: str | None = None,
        changelog: str | None = None,
    ) -> PublishResult:
        """
        发布应用

        :param db: 数据库会话
        :param bump: 版本递增类型 (patch/minor/major)
        :param version: 指定版本号（与 bump 互斥）
        :param changelog: 版本更新日志
        :return: 发布结果
        """
        # 1. 验证应用包
        print_info('验证应用包...')
        result = self.validator.validate()
        if not result.valid:
            self.validator.print_result()
            return PublishResult(success=False, error='应用包验证失败')

        manifest = self.validator.manifest
        if not manifest:
            return PublishResult(success=False, error='无法获取应用清单')

        app_id = manifest.id

        # 2. 处理版本号
        print_info('处理版本号...')
        final_version = await self._resolve_version(db, app_id, manifest.version, bump, version)
        if not final_version:
            return PublishResult(success=False, error='版本号处理失败')

        # 3. 检查版本是否已存在
        existing_version = await self._get_version(db, app_id, final_version)
        if existing_version:
            return PublishResult(
                success=False,
                error=f'版本 {final_version} 已存在，请使用其他版本号'
            )

        # 4. 打包前更新 manifest.json 版本号（确保包内版本与数据库一致）
        if manifest.version != final_version:
            self._update_manifest_version(final_version)

        # 5. 打包
        print_info(f'打包应用 (v{final_version})...')
        package_result = self.packager.package()
        print_success(f'打包完成: {package_result.file_count} 个文件, {format_size(package_result.file_size)}')

        # 6. 上传到 S3
        print_info('上传到存储...')
        try:
            package_url, file_hash, file_size = await marketplace_storage_service.upload_app_package(
                db=db,
                app_id=app_id,
                version=final_version,
                content=package_result.content,
            )
            print_success(f'上传完成: {package_url}')
        except Exception as e:
            return PublishResult(success=False, error=f'上传失败: {e}')

        # 7. 上传图标（带版本后缀避免 CDN 缓存）
        icon_url = None
        icon_paths = [
            self.app_path / 'icon.svg',
            self.app_path / 'assets' / 'icon.svg',
        ]
        for icon_path in icon_paths:
            if icon_path.exists():
                print_info('上传图标...')
                try:
                    icon_content = icon_path.read_bytes()
                    icon_url = await marketplace_storage_service.upload_icon(
                        db=db,
                        item_type='app',
                        item_id=app_id,
                        content=icon_content,
                        version=final_version,
                    )
                    print_success('图标上传完成')
                except Exception as e:
                    print_error(f'图标上传失败: {e}')
                break

        # 8. 创建/更新数据库记录
        print_info('更新数据库...')
        try:
            existing_app = await self._get_app(db, app_id)

            if existing_app:
                await self._update_app(db, app_id, manifest, icon_url)
            else:
                await self._create_app(db, manifest, icon_url)

            # 将旧版本的 is_latest 设为 False
            await self._clear_latest_flag(db, app_id)

            # 创建新版本记录
            await self._create_version(
                db=db,
                app_id=app_id,
                version=final_version,
                changelog=changelog,
                package_url=package_url,
                file_hash=file_hash,
                file_size=file_size,
                skill_dependencies=manifest.skill_dependencies,
            )

            print_success('数据库更新完成')

        except Exception as e:
            return PublishResult(success=False, error=f'数据库更新失败: {e}')

        return PublishResult(
            success=True,
            app_id=app_id,
            version=final_version,
            package_url=package_url,
            file_hash=file_hash,
            file_size=file_size,
        )

    async def _resolve_version(
        self,
        db: AsyncSession,
        app_id: str,
        manifest_version: str,
        bump: str | None,
        explicit_version: str | None,
    ) -> str | None:
        """解析最终版本号"""
        if explicit_version:
            try:
                VersionInfo.parse(explicit_version)
                return explicit_version
            except ValueError as e:
                print_error(str(e))
                return None

        if bump:
            latest = await self._get_latest_version(db, app_id)
            if latest:
                try:
                    current = VersionInfo.parse(latest.version)
                    new_version = current.bump(bump)
                    return str(new_version)
                except ValueError as e:
                    print_error(str(e))
                    return None
            else:
                return manifest_version

        return manifest_version

    def _update_manifest_version(self, version: str) -> None:
        """更新 manifest.json 中的版本号"""
        manifest_path = self.app_path / 'manifest.json'
        if not manifest_path.exists():
            return

        try:
            with open(manifest_path, encoding='utf-8') as f:
                data = json.load(f)

            data['version'] = version

            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print_info(f'已更新 manifest.json 版本号为 {version}')
        except Exception as e:
            print_error(f'更新 manifest.json 版本号失败: {e}')

    async def _get_app(self, db: AsyncSession, app_id: str) -> MarketplaceTemplate | None:
        """获取应用"""
        stmt = select(MarketplaceTemplate).where(MarketplaceTemplate.app_id == app_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_version(self, db: AsyncSession, app_id: str, version: str) -> MarketplaceTemplateVersion | None:
        """获取应用版本"""
        stmt = select(MarketplaceTemplateVersion).where(
            MarketplaceTemplateVersion.app_id == app_id,
            MarketplaceTemplateVersion.version == version,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_latest_version(self, db: AsyncSession, app_id: str) -> MarketplaceTemplateVersion | None:
        """获取最新版本"""
        stmt = select(MarketplaceTemplateVersion).where(
            MarketplaceTemplateVersion.app_id == app_id,
            MarketplaceTemplateVersion.is_latest,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _create_app(self, db: AsyncSession, manifest, icon_url: str | None) -> None:
        """创建新应用"""
        from decimal import Decimal
        # 将技能依赖列表转为逗号分隔字符串
        skill_deps_str = ','.join(manifest.skill_dependencies) if manifest.skill_dependencies else None

        app = MarketplaceTemplate(
            app_id=manifest.id,
            name=manifest.name,
            description=manifest.description,
            icon_url=icon_url,
            emoji=getattr(manifest, 'emoji', None),
            author_name=manifest.author_name,
            pricing_type=manifest.pricing_type,
            price=Decimal(0),
            is_private=False,
            is_official=False,
            download_count=0,
            skill_dependencies=skill_deps_str,
        )
        db.add(app)
        await db.flush()

    async def _update_app(self, db: AsyncSession, app_id: str, manifest, icon_url: str | None) -> None:
        """更新已有应用"""
        skill_deps_str = ','.join(manifest.skill_dependencies) if manifest.skill_dependencies else None

        update_data = {
            'name': manifest.name,
            'description': manifest.description,
            'pricing_type': manifest.pricing_type,
            'skill_dependencies': skill_deps_str,
            'emoji': getattr(manifest, 'emoji', None),
        }
        if icon_url:
            update_data['icon_url'] = icon_url

        stmt = update(MarketplaceTemplate).where(MarketplaceTemplate.app_id == app_id).values(**update_data)
        await db.execute(stmt)

    async def _clear_latest_flag(self, db: AsyncSession, app_id: str) -> None:
        """清除旧版本的 is_latest 标志"""
        stmt = update(MarketplaceTemplateVersion).where(
            MarketplaceTemplateVersion.app_id == app_id,
            MarketplaceTemplateVersion.is_latest,
        ).values(is_latest=False)
        await db.execute(stmt)

    async def _create_version(
        self,
        db: AsyncSession,
        app_id: str,
        version: str,
        changelog: str | None,
        package_url: str,
        file_hash: str,
        file_size: int,
        skill_dependencies: list[str],
    ) -> None:
        """创建版本记录"""
        # 技能依赖存为 JSON 格式
        skill_deps_versioned = {}
        for dep in skill_dependencies:
            if '@' in dep:
                skill_id, ver = dep.split('@', 1)
                skill_deps_versioned[skill_id] = ver
            else:
                skill_deps_versioned[dep] = '*'

        app_version = MarketplaceTemplateVersion(
            app_id=app_id,
            version=version,
            changelog=changelog,
            skill_dependencies_versioned=skill_deps_versioned or None,
            package_url=package_url,
            file_hash=file_hash,
            file_size=file_size,
            is_latest=True,
        )
        db.add(app_version)
        await db.flush()
