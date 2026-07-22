"""技能发布服务

处理技能的验证、打包、上传和数据库记录创建。
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model import MarketplaceSkill, MarketplaceSkillVersion
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.cli_tools.cli.common import (
    VersionInfo,
    format_size,
    print_error,
    print_info,
    print_success,
)
from backend.cli_tools.packager.skill_packager import SkillPackager
from backend.cli_tools.validator.skill_validator import SkillConfig, SkillValidator

BumpType = Literal['patch', 'minor', 'major']


@dataclass
class PublishResult:
    """发布结果"""

    success: bool
    skill_id: str = ''
    version: str = ''
    package_url: str = ''
    file_hash: str = ''
    file_size: int = 0
    error: str = ''


class SkillPublisher:
    """技能发布服务"""

    def __init__(self, skill_path: Path) -> None:
        self.skill_path = Path(skill_path).resolve()
        self.validator = SkillValidator(skill_path)
        self.packager = SkillPackager(skill_path)

    async def publish(  # ruff:ignore[complex-structure]
        self,
        db: AsyncSession,
        bump: BumpType | None = None,
        version: str | None = None,
        changelog: str | None = None,
    ) -> PublishResult:
        """
        发布技能

        :param db: 数据库会话
        :param bump: 版本递增类型 (patch/minor/major)
        :param version: 指定版本号（与 bump 互斥）
        :param changelog: 版本更新日志
        :return: 发布结果
        """
        # 1. 验证技能包
        print_info('验证技能包...')
        result = self.validator.validate()
        if not result.valid:
            self.validator.print_result()
            return PublishResult(success=False, error='技能包验证失败')

        config = self.validator.config
        if not config:
            return PublishResult(success=False, error='无法获取技能配置')

        skill_id = config.id

        # 2. 处理版本号
        print_info('处理版本号...')
        final_version = await self._resolve_version(db, skill_id, config.version, bump, version)
        if not final_version:
            return PublishResult(success=False, error='版本号处理失败')

        # 3. 检查版本是否已存在
        existing_version = await self._get_version(db, skill_id, final_version)
        if existing_version:
            return PublishResult(success=False, error=f'版本 {final_version} 已存在，请使用其他版本号')

        # 4. 打包前更新 config.yaml 版本号（确保包内版本与数据库一致）
        if config.version != final_version:
            self._update_config_version(final_version)

        # 5. 打包
        print_info(f'打包技能 (v{final_version})...')
        package_result = self.packager.package()
        print_success(f'打包完成: {package_result.file_count} 个文件, {format_size(package_result.file_size)}')

        # 6. 上传到 S3
        print_info('上传到存储...')
        try:
            package_url, file_hash, file_size = await marketplace_storage_service.upload_skill_package(
                db=db,
                skill_id=skill_id,
                version=final_version,
                content=package_result.content,
            )
            print_success(f'上传完成: {package_url}')
        except Exception as e:
            return PublishResult(success=False, error=f'上传失败: {e}')

        # 7. 上传图标（带版本后缀避免 CDN 缓存）
        icon_path = self.skill_path / 'icon.svg'
        if icon_path.exists():
            print_info('上传图标...')
            try:
                icon_content = icon_path.read_bytes()
                icon_url = await marketplace_storage_service.upload_icon(
                    db=db,
                    item_type='skill',
                    item_id=skill_id,
                    content=icon_content,
                    version=final_version,
                )
                print_success('图标上传完成')
            except Exception as e:
                print_error(f'图标上传失败: {e}')
                icon_url = None
        else:
            icon_url = None

        # 8. 创建/更新数据库记录
        print_info('更新数据库...')
        try:
            # 检查技能是否已存在
            existing_skill = await self._get_skill(db, skill_id)

            if existing_skill:
                # 更新已有技能
                await self._update_skill(db, skill_id, config, icon_url)
            else:
                # 创建新技能
                await self._create_skill(db, config, icon_url)

            # 将旧版本的 is_latest 设为 False
            await self._clear_latest_flag(db, skill_id)

            # 创建新版本记录
            await self._create_version(
                db=db,
                skill_id=skill_id,
                version=final_version,
                changelog=changelog,
                package_url=package_url,
                file_hash=file_hash,
                file_size=file_size,
            )

            print_success('数据库更新完成')

        except Exception as e:
            return PublishResult(success=False, error=f'数据库更新失败: {e}')

        return PublishResult(
            success=True,
            skill_id=skill_id,
            version=final_version,
            package_url=package_url,
            file_hash=file_hash,
            file_size=file_size,
        )

    async def _resolve_version(
        self,
        db: AsyncSession,
        skill_id: str,
        config_version: str,
        bump: BumpType | None,
        explicit_version: str | None,
    ) -> str | None:
        """解析最终版本号"""
        if explicit_version:
            # 使用显式指定的版本号
            try:
                VersionInfo.parse(explicit_version)
            except ValueError as e:
                print_error(str(e))
                return None
            else:
                return explicit_version

        if bump:
            # 获取最新版本并递增
            latest = await self._get_latest_version(db, skill_id)
            if latest:
                try:
                    current = VersionInfo.parse(latest.version)
                    new_version = current.bump(bump)
                    return str(new_version)
                except ValueError as e:
                    print_error(str(e))
                    return None
            else:
                # 新技能，使用配置中的版本号
                return config_version

        # 使用配置中的版本号
        return config_version

    def _update_config_version(self, version: str) -> None:
        """更新 config.yaml 中的版本号"""
        config_path = self.skill_path / 'config.yaml'
        if not config_path.exists():
            return

        try:
            content = Path(config_path).read_text(encoding='utf-8')

            # 使用正则替换 version 字段
            import re

            new_content = re.sub(r'^version:\s*.+$', f'version: {version}', content, flags=re.MULTILINE)

            Path(config_path).write_text(new_content, encoding='utf-8')

            print_info(f'已更新 config.yaml 版本号为 {version}')
        except Exception as e:
            print_error(f'更新 config.yaml 版本号失败: {e}')

    async def _get_skill(self, db: AsyncSession, skill_id: str) -> MarketplaceSkill | None:
        """获取技能"""
        stmt = select(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_version(self, db: AsyncSession, skill_id: str, version: str) -> MarketplaceSkillVersion | None:
        """获取技能版本"""
        stmt = select(MarketplaceSkillVersion).where(
            MarketplaceSkillVersion.skill_id == skill_id,
            MarketplaceSkillVersion.version == version,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_latest_version(self, db: AsyncSession, skill_id: str) -> MarketplaceSkillVersion | None:
        """获取最新版本"""
        stmt = select(MarketplaceSkillVersion).where(
            MarketplaceSkillVersion.skill_id == skill_id,
            MarketplaceSkillVersion.is_latest,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _create_skill(self, db: AsyncSession, config: SkillConfig, icon_url: str | None) -> None:
        """创建新技能"""
        skill = MarketplaceSkill(
            skill_id=config.id,
            name=config.name,
            name_en=config.name,
            name_zh=config.name,
            description_en=config.description,
            description_zh=config.description,
            icon_url=icon_url,
            author_name=config.author_name,
            category=config.category,
            tags=config.tags,
            pricing_type=config.pricing,
            price=Decimal(0),
            is_private=False,
            is_official=False,
            download_count=0,
        )
        db.add(skill)
        await db.flush()

    async def _update_skill(
        self,
        db: AsyncSession,
        skill_id: str,
        config: SkillConfig,
        icon_url: str | None,
    ) -> None:
        """更新已有技能"""
        update_data = {
            'name': config.name,
            'name_en': config.name,
            'name_zh': config.name,
            'description_en': config.description,
            'description_zh': config.description,
            'category': config.category,
            'tags': config.tags,
            'pricing_type': config.pricing,
        }
        if icon_url:
            update_data['icon_url'] = icon_url

        stmt = update(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id).values(**update_data)
        await db.execute(stmt)

    async def _clear_latest_flag(self, db: AsyncSession, skill_id: str) -> None:
        """清除旧版本的 is_latest 标志"""
        stmt = (
            update(MarketplaceSkillVersion)
            .where(
                MarketplaceSkillVersion.skill_id == skill_id,
                MarketplaceSkillVersion.is_latest,
            )
            .values(is_latest=False)
        )
        await db.execute(stmt)

    async def _create_version(
        self,
        db: AsyncSession,
        skill_id: str,
        version: str,
        changelog: str | None,
        package_url: str,
        file_hash: str,
        file_size: int,
    ) -> None:
        """创建版本记录"""
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
        await db.flush()
