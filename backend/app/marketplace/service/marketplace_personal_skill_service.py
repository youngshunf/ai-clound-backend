"""个人技能库 service（个人技能管理 SSOT，SKILLSYNC-C2）。

个人技能 = owner 维度的个人技能库，收纳 runtime-auto（分身运行时自学）/ user-upload
（本地目录上传）/ agent-published（分身主动发布）三来源；与 marketplace_skill（公开市场列表）
分离、与 hasn_agents.skills（agent 技能装配）解耦。

核心方法：
  - upsert_from_package：从技能 zip 包幂等 upsert（content_hash 未变则跳过 S3/版本，纯去重）。
  - list_mine / get_mine / delete_mine：owner 维度增查删。
  - get_package_for_owner：取包字节（agent 鉴权下载用，校验 owner 归属）。
  - promote_to_market：一键发布到市场（复用 marketplace_skill 上传流 + 回填 published_skill_id）。

与 marketplace_skill 复用：S3 私有桶打包（upload_skill_package）、zip 解析（parse_skill_package）、
正文提取（extract_skill_body）。
"""

from __future__ import annotations

import hashlib
import json
import zipfile

from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model.marketplace_personal_skill import MarketplacePersonalSkill
from backend.app.marketplace.service.package_validation import parse_skill_package
from backend.app.marketplace.service.resource_id import (
    build_resource_id,
    slug_from_candidate,
    validate_slug,
)
from backend.app.marketplace.service.skill_content_extractor import extract_skill_body
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.exception import errors
from backend.utils.timezone import timezone

# 个人技能包按 S3 既有技能包路径存储（item_type='skill'，version 用合成 semver）。
_S3_SEMVER = '{0}.0.0'
_VALID_ORIGINS = {'runtime-auto', 'user-upload', 'agent-published'}


def _tags_to_json(tags: Any) -> str | None:
    if not tags:
        return None
    if isinstance(tags, str):
        values = [tags]
    elif isinstance(tags, list):
        values = [str(t).strip() for t in tags]
    else:
        values = [str(tags).strip()]
    cleaned = [v for v in values if v]
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def _extract_body_and_files(content: bytes) -> tuple[str | None, str]:
    """从技能 zip 取 SKILL.md 正文（去 frontmatter）+ 文件清单 [{path,size}]（仅名称+大小）。"""
    body: str | None = None
    files: list[dict[str, Any]] = []
    with zipfile.ZipFile(BytesIO(content), 'r') as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            files.append({'path': info.filename, 'size': info.file_size})
            if info.filename == 'SKILL.md':
                try:
                    raw = zf.read(info.filename).decode('utf-8')
                    body = extract_skill_body(raw) or None
                except (UnicodeDecodeError, KeyError):
                    body = None
    files.sort(key=lambda f: f['path'])
    return body, json.dumps(files, ensure_ascii=False)


class PersonalSkillService:
    @staticmethod
    def format(skill: MarketplacePersonalSkill) -> dict[str, Any]:
        return {
            'personal_skill_id': skill.personal_skill_id,
            'user_id': skill.user_id,
            'hasn_id': skill.hasn_id,
            'slug': skill.slug,
            'name': skill.name,
            'description': skill.description,
            'origin': skill.origin,
            'source_agent_hasn_id': skill.source_agent_hasn_id,
            'content_hash': skill.content_hash,
            'version': skill.version,
            'visibility': skill.visibility,
            'published_skill_id': skill.published_skill_id,
            'icon_url': skill.icon_url,
            'emoji': skill.emoji,
            'category': skill.category,
            'tags': skill.tags,
            'file_size': skill.file_size,
            'synced_at': skill.synced_at.isoformat() if skill.synced_at else None,
            'created_time': skill.created_time.isoformat() if skill.created_time else None,
            'updated_time': skill.updated_time.isoformat() if skill.updated_time else None,
        }

    @staticmethod
    async def _get_by_user_slug(
        db: AsyncSession, *, user_id: int, slug: str
    ) -> MarketplacePersonalSkill | None:
        return (
            await db.execute(
                select(MarketplacePersonalSkill).where(
                    (MarketplacePersonalSkill.user_id == user_id)
                    & (MarketplacePersonalSkill.slug == slug)
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_mine(
        db: AsyncSession, *, user_id: int, personal_skill_id: str
    ) -> MarketplacePersonalSkill:
        skill = (
            await db.execute(
                select(MarketplacePersonalSkill).where(
                    (MarketplacePersonalSkill.user_id == user_id)
                    & (MarketplacePersonalSkill.personal_skill_id == personal_skill_id)
                )
            )
        ).scalar_one_or_none()
        if skill is None:
            raise errors.NotFoundError(msg='ERR_PERSONAL_SKILL_NOT_FOUND')
        return skill

    @staticmethod
    async def list_mine(db: AsyncSession, *, user_id: int) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(MarketplacePersonalSkill)
                .where(MarketplacePersonalSkill.user_id == user_id)
                .order_by(MarketplacePersonalSkill.updated_time.desc().nulls_last())
            )
        ).scalars().all()
        return [PersonalSkillService.format(r) for r in rows]

    @staticmethod
    async def upsert_from_package(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        content: bytes,
        origin: str = 'user-upload',
        source_agent_hasn_id: str | None = None,
        content_hash: str | None = None,
        slug: str | None = None,
    ) -> MarketplacePersonalSkill:
        if not hasn_id:
            raise errors.AuthorizationError(msg='用户未注册 HASN 身份')
        if origin not in _VALID_ORIGINS:
            raise errors.RequestError(msg=f'非法 origin: {origin}')

        package = parse_skill_package(content)  # 校验 SKILL.md frontmatter（name/description 必填）
        metadata = package.metadata
        final_slug = (
            validate_slug(slug)
            if slug
            else slug_from_candidate(metadata.get('slug') or metadata.get('id'), 'skill')
        )
        namespace = f'user/{hasn_id}'
        personal_skill_id = build_resource_id(namespace, final_slug)
        effective_hash = content_hash or hashlib.sha256(content).hexdigest()
        body, files_json = _extract_body_and_files(content)

        existing = await PersonalSkillService._get_by_user_slug(db, user_id=user_id, slug=final_slug)
        # 幂等去重：内容哈希未变且已落包 → 直接返回，不重传 S3、不 bump 版本。
        if existing and existing.content_hash == effective_hash and existing.package_url:
            return existing

        version = (existing.version + 1) if existing else 1
        semver = _S3_SEMVER.format(version)
        package_url, file_hash, file_size = await marketplace_storage_service.upload_skill_package(
            db=db, skill_id=personal_skill_id, version=semver, content=content
        )
        icon_url = existing.icon_url if existing else None
        if package.icon:
            icon_url = await marketplace_storage_service.upload_icon(
                db=db,
                item_type='skill',
                item_id=personal_skill_id,
                content=package.icon.content,
                filename=package.icon.filename,
            )
        now = timezone.now()
        if existing:
            existing.name = str(metadata.get('name'))
            existing.description = metadata.get('description')
            existing.body = body
            existing.files = files_json
            existing.origin = origin
            existing.source_agent_hasn_id = source_agent_hasn_id
            existing.content_hash = effective_hash
            existing.version = version
            existing.package_url = package_url
            existing.file_hash = file_hash
            existing.file_size = file_size
            existing.icon_url = icon_url
            existing.emoji = metadata.get('emoji')
            existing.category = metadata.get('category')
            existing.tags = _tags_to_json(metadata.get('tags'))
            existing.synced_at = now
            skill = existing
        else:
            skill = MarketplacePersonalSkill(
                personal_skill_id=personal_skill_id,
                user_id=user_id,
                hasn_id=hasn_id,
                slug=final_slug,
                name=str(metadata.get('name')),
                description=metadata.get('description'),
                body=body,
                files=files_json,
                origin=origin,
                source_agent_hasn_id=source_agent_hasn_id,
                content_hash=effective_hash,
                version=version,
                package_url=package_url,
                file_hash=file_hash,
                file_size=file_size,
                icon_url=icon_url,
                emoji=metadata.get('emoji'),
                category=metadata.get('category'),
                tags=_tags_to_json(metadata.get('tags')),
                visibility='private',
                synced_at=now,
            )
            db.add(skill)
        await db.flush()
        return skill

    @staticmethod
    async def get_package_for_owner(
        db: AsyncSession, *, owner_user_id: int, personal_skill_id: str
    ) -> tuple[MarketplacePersonalSkill, bytes]:
        """取包字节（agent 鉴权下载用）。严格校验 personal skill 属于该 owner，否则 404。"""
        skill = await PersonalSkillService.get_mine(
            db, user_id=owner_user_id, personal_skill_id=personal_skill_id
        )
        content = await marketplace_storage_service.download_package(
            db=db,
            item_type='skill',
            item_id=skill.personal_skill_id,
            version=_S3_SEMVER.format(skill.version),
        )
        return skill, content

    @staticmethod
    async def delete_mine(db: AsyncSession, *, user_id: int, personal_skill_id: str) -> None:
        skill = await PersonalSkillService.get_mine(
            db, user_id=user_id, personal_skill_id=personal_skill_id
        )
        await db.delete(skill)
        await db.flush()

    @staticmethod
    async def promote_to_market(
        db: AsyncSession,
        *,
        user_id: int,
        hasn_id: str,
        personal_skill_id: str,
        changelog: str | None = None,
    ) -> dict[str, Any]:
        """一键发布到市场：取个人技能包 → 复用 marketplace 上传流建/更新 listing → 回填 published_skill_id。"""
        from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service

        skill, content = await PersonalSkillService.get_package_for_owner(
            db, owner_user_id=user_id, personal_skill_id=personal_skill_id
        )
        market_skill = await marketplace_skill_service.upload_user_skill(
            db=db,
            user_id=user_id,
            hasn_id=hasn_id,
            content=content,
            filename=f'{skill.slug}.zip',
            slug=skill.slug,
            changelog=changelog,
        )
        # upload_user_skill 内部已 commit；这里在同一会话再设 published_skill_id 并提交。
        skill.published_skill_id = market_skill.skill_id
        await db.commit()
        await db.refresh(skill)
        return {
            'personal_skill': PersonalSkillService.format(skill),
            'market_skill_id': market_skill.skill_id,
        }


personal_skill_service: PersonalSkillService = PersonalSkillService()
