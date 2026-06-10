"""通用网页发布与分享 service（owner 隔离 + slug + 可见性 + 口令 + 过期 + 访问票）。

设计事实源：docs/hasn-node设计文档/18-通用网页发布与分享/{01-数据模型与权限,03-工具与接口}.md。
- site/revision 双写：create=site+rev1+移动 current_revision_id；update=新 rev（content_hash 去重）+移动指针。
- 所有读写强制 owner_id 隔离（agent 代发布也落 owner 名下）。
- 可见性序 private<password<unlisted<public；password 进出清空/写 hash。
- 浏览器侧 private 访问票：短时签名 JWT，绑定 site_id（[01] §3.1）。
"""

from __future__ import annotations

import secrets

from datetime import datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.publish.model import Revision, Site
from backend.common.exception import errors
from backend.core.conf import settings
from backend.utils.timezone import timezone

# ---- 常量 ----
_SLUG_ALPHABET = '23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ'  # 去易混字符
_SLUG_LEN = 12
_SLUG_MAX_RETRY = 6
VISIBILITY_ORDER = ('private', 'password', 'unlisted', 'public')
VALID_KINDS = ('deck', 'report', 'page', 'dashboard', 'other')
MAX_REVISIONS_PER_SITE = 20
VIEW_TICKET_TTL_SECONDS = 600  # 10 分钟
_VIEW_TICKET_TYPE = 'publish_view_ticket'

_password_hasher = PasswordHash((BcryptHasher(),))


def visibility_rank(visibility: str) -> int:
    """可见性级别（越高越公开）；未知视作 private（0）。"""
    try:
        return VISIBILITY_ORDER.index(visibility)
    except ValueError:
        return 0


def _gen_slug() -> str:
    return ''.join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LEN))


def hash_password(plain: str) -> str:
    return _password_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _password_hasher.verify(plain, hashed)
    except Exception:
        return False


def site_to_dict(site: Site) -> dict[str, Any]:
    """序列化 site（不含 password_hash 等敏感字段）。"""
    return {
        'id': site.id,
        'owner_id': site.owner_id,
        'publisher_agent_id': site.publisher_agent_id,
        'kind': site.kind,
        'title': site.title,
        'slug': site.slug,
        'source_app': site.source_app,
        'source_ref': site.source_ref,
        'current_revision_id': site.current_revision_id,
        'status': site.status,
        'visibility': site.visibility,
        'has_password': bool(site.password_hash),
        'expires_at': timezone.to_str(site.expires_at) if site.expires_at else None,
        'allow_present': site.allow_present,
        'allow_download': site.allow_download,
        'allow_indexing': site.allow_indexing,
        'view_count': site.view_count,
        'rev': site.rev,
        'created_time': timezone.to_str(site.created_time) if site.created_time else None,
        'updated_time': timezone.to_str(site.updated_time) if site.updated_time else None,
    }


def revision_to_dict(rev: Revision) -> dict[str, Any]:
    return {
        'id': rev.id,
        'site_id': rev.site_id,
        'seq': rev.seq,
        'asset_id': rev.asset_id,
        'runtime': rev.runtime,
        'content_hash': rev.content_hash,
        'size_bytes': rev.size_bytes,
        'manifest_json': rev.manifest_json,
        'created_time': timezone.to_str(rev.created_time) if rev.created_time else None,
    }


class PublishService:
    """owner 隔离的发布服务。所有方法第一参数 db，关键字 owner_id 为隔离键。"""

    # ---------------- 校验 ----------------

    @staticmethod
    def _validate_visibility(visibility: str, password: str | None) -> None:
        if visibility not in VISIBILITY_ORDER:
            raise errors.RequestError(msg=f'非法可见性：{visibility}')
        if visibility == 'password' and not password:
            raise errors.RequestError(msg='可见性为 password 时必须提供口令')

    @staticmethod
    def _validate_kind(kind: str) -> str:
        return kind if kind in VALID_KINDS else 'other'

    # ---------------- slug ----------------

    @staticmethod
    async def _alloc_slug(db: AsyncSession) -> str:
        for _ in range(_SLUG_MAX_RETRY):
            slug = _gen_slug()
            exists = (
                await db.execute(select(Site.id).where(Site.slug == slug).limit(1))
            ).scalar_one_or_none()
            if exists is None:
                return slug
        raise errors.ServerError(msg='slug 分配失败（多次冲突）')

    # ---------------- create ----------------

    @staticmethod
    async def create_site(
        db: AsyncSession,
        *,
        owner_id: str,
        publisher_agent_id: str | None = None,
        kind: str = 'page',
        title: str = '',
        asset_id: str,
        runtime: str = 'single-html',
        content_hash: str = '',
        size_bytes: int = 0,
        manifest_json: dict | None = None,
        visibility: str = 'private',
        password: str | None = None,
        expires_at: datetime | None = None,
        allow_present: bool = True,
        allow_download: bool = False,
        allow_indexing: bool = False,
        source_app: str | None = None,
        source_ref: str | None = None,
    ) -> dict[str, Any]:
        PublishService._validate_visibility(visibility, password)
        if not asset_id:
            raise errors.RequestError(msg='缺少制品 asset_id')
        slug = await PublishService._alloc_slug(db)
        site = Site(
            owner_id=owner_id,
            publisher_agent_id=publisher_agent_id,
            kind=PublishService._validate_kind(kind),
            title=title or '未命名',
            slug=slug,
            source_app=source_app,
            source_ref=source_ref,
            current_revision_id=None,
            status='active',
            visibility=visibility,
            password_hash=hash_password(password) if (visibility == 'password' and password) else None,
            expires_at=expires_at,
            allow_present=allow_present,
            allow_download=allow_download,
            allow_indexing=allow_indexing if visibility == 'public' else False,
            view_count=0,
            rev=1,
        )
        db.add(site)
        await db.flush()  # 取 site.id

        revision = Revision(
            site_id=site.id,
            owner_id=owner_id,
            seq=1,
            asset_id=asset_id,
            runtime=runtime,
            content_hash=content_hash,
            size_bytes=size_bytes,
            manifest_json=manifest_json,
        )
        db.add(revision)
        await db.flush()  # 取 revision.id

        site.current_revision_id = revision.id
        await db.flush()
        return {'site': site_to_dict(site), 'revision': revision_to_dict(revision)}

    # ---------------- update（新 revision） ----------------

    @staticmethod
    async def update_site(
        db: AsyncSession,
        *,
        owner_id: str,
        site_id: int,
        asset_id: str,
        runtime: str = 'single-html',
        content_hash: str = '',
        size_bytes: int = 0,
        manifest_json: dict | None = None,
    ) -> dict[str, Any]:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        if not asset_id:
            raise errors.RequestError(msg='缺少制品 asset_id')

        # content_hash 去重：site 内同 hash 复用 revision（仅移动指针）
        if content_hash:
            dup = (
                await db.execute(
                    select(Revision)
                    .where(
                        Revision.site_id == site.id,
                        Revision.content_hash == content_hash,
                        Revision.deleted_time.is_(None),
                    )
                    .order_by(Revision.seq.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if dup is not None:
                site.current_revision_id = dup.id
                site.status = 'active'
                site.rev += 1
                await db.flush()
                return {'site': site_to_dict(site), 'revision': revision_to_dict(dup), 'reused': True}

        next_seq = (
            await db.execute(select(func.coalesce(func.max(Revision.seq), 0)).where(Revision.site_id == site.id))
        ).scalar_one() + 1
        revision = Revision(
            site_id=site.id,
            owner_id=owner_id,
            seq=next_seq,
            asset_id=asset_id,
            runtime=runtime,
            content_hash=content_hash,
            size_bytes=size_bytes,
            manifest_json=manifest_json,
        )
        db.add(revision)
        await db.flush()
        site.current_revision_id = revision.id
        site.status = 'active'  # update 自动复活已撤销 site
        site.rev += 1
        await db.flush()
        await PublishService._gc_revisions(db, site_id=site.id, keep_id=revision.id)
        return {'site': site_to_dict(site), 'revision': revision_to_dict(revision), 'reused': False}

    @staticmethod
    async def _gc_revisions(db: AsyncSession, *, site_id: int, keep_id: int) -> None:
        """保留每 site 最近 MAX_REVISIONS_PER_SITE 个 revision（软删超出的旧版本，当前指针除外）。"""
        rows = (
            (
                await db.execute(
                    select(Revision.id)
                    .where(Revision.site_id == site_id, Revision.deleted_time.is_(None))
                    .order_by(Revision.seq.desc())
                )
            )
            .scalars()
            .all()
        )
        for stale_id in rows[MAX_REVISIONS_PER_SITE:]:
            if stale_id == keep_id:
                continue
            rev = await db.get(Revision, stale_id)
            if rev is not None:
                rev.deleted_time = timezone.now()
        await db.flush()

    # ---------------- visibility / revoke / delete ----------------

    @staticmethod
    async def set_visibility(
        db: AsyncSession,
        *,
        owner_id: str,
        site_id: int,
        visibility: str | None = None,
        password: str | None = None,
        clear_password: bool = False,
        expires_at: datetime | None = None,
        clear_expires: bool = False,
        allow_present: bool | None = None,
        allow_download: bool | None = None,
        allow_indexing: bool | None = None,
    ) -> dict[str, Any]:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        target_vis = visibility if visibility is not None else site.visibility
        # 进入 password 必须有口令（或已有 hash）
        if target_vis == 'password' and not (password or site.password_hash):
            raise errors.RequestError(msg='切换到 password 可见性必须提供口令')
        if visibility is not None:
            if visibility not in VISIBILITY_ORDER:
                raise errors.RequestError(msg=f'非法可见性：{visibility}')
            site.visibility = visibility
            # 不变量 3：离开 password 清空 hash
            if visibility != 'password':
                site.password_hash = None
        if password:
            site.password_hash = hash_password(password)
        elif clear_password and site.visibility != 'password':
            site.password_hash = None
        if clear_expires:
            site.expires_at = None
        elif expires_at is not None:
            site.expires_at = expires_at
        if allow_present is not None:
            site.allow_present = allow_present
        if allow_download is not None:
            site.allow_download = allow_download
        if allow_indexing is not None:
            site.allow_indexing = allow_indexing if site.visibility == 'public' else False
        if site.visibility != 'public':
            site.allow_indexing = False
        site.rev += 1
        await db.flush()
        return site_to_dict(site)

    @staticmethod
    async def revoke(db: AsyncSession, *, owner_id: str, site_id: int) -> dict[str, Any]:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        site.status = 'revoked'
        site.current_revision_id = None
        site.rev += 1
        await db.flush()
        return site_to_dict(site)

    @staticmethod
    async def delete_site(db: AsyncSession, *, owner_id: str, site_id: int) -> None:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        site.deleted_time = timezone.now()
        site.status = 'revoked'
        site.rev += 1
        await db.flush()

    # ---------------- 读 ----------------

    @staticmethod
    async def _get_owned(db: AsyncSession, *, owner_id: str, site_id: int) -> Site:
        site = (
            await db.execute(
                select(Site).where(Site.id == site_id, Site.owner_id == owner_id, Site.deleted_time.is_(None))
            )
        ).scalar_one_or_none()
        if site is None:
            raise errors.NotFoundError(msg='发布不存在')
        return site

    @staticmethod
    async def get_owned(db: AsyncSession, *, owner_id: str, site_id: int) -> dict[str, Any]:
        return site_to_dict(await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id))

    @staticmethod
    async def list_owned(
        db: AsyncSession, *, owner_id: str, kind: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        base = select(Site).where(Site.owner_id == owner_id, Site.deleted_time.is_(None))
        if kind:
            base = base.where(Site.kind == kind)
        total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (
            (
                await db.execute(
                    base.order_by(Site.updated_time.desc().nullslast(), Site.id.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return {'items': [site_to_dict(s) for s in rows], 'total': int(total)}

    @staticmethod
    async def get_by_source(
        db: AsyncSession, *, owner_id: str, source_app: str, source_ref: str
    ) -> dict[str, Any] | None:
        site = (
            await db.execute(
                select(Site).where(
                    Site.owner_id == owner_id,
                    Site.source_app == source_app,
                    Site.source_ref == source_ref,
                    Site.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()
        return site_to_dict(site) if site else None

    @staticmethod
    async def get_current_revision(db: AsyncSession, *, site_id: int) -> Revision | None:
        site = await db.get(Site, site_id)
        if site is None or site.current_revision_id is None:
            return None
        return await db.get(Revision, site.current_revision_id)

    # ---------------- 浏览器访问票（private，[01] §3.1） ----------------

    @staticmethod
    def issue_view_ticket(*, site_id: int, owner_id: str) -> dict[str, Any]:
        exp = timezone.now() + timedelta(seconds=VIEW_TICKET_TTL_SECONDS)
        payload = {
            'typ': _VIEW_TICKET_TYPE,
            'site_id': site_id,
            'owner_id': owner_id,
            'exp': timezone.to_utc(exp).timestamp(),
        }
        ticket = jwt.encode(payload, settings.TOKEN_SECRET_KEY, settings.TOKEN_ALGORITHM)
        return {'ticket': ticket, 'expires_at': timezone.to_str(exp), 'ttl_seconds': VIEW_TICKET_TTL_SECONDS}

    @staticmethod
    def verify_view_ticket(ticket: str, *, site_id: int) -> bool:
        try:
            claims = jwt.decode(
                ticket,
                settings.TOKEN_SECRET_KEY,
                algorithms=[settings.TOKEN_ALGORITHM],
                options={'verify_exp': True},
            )
        except JWTError:
            return False
        return claims.get('typ') == _VIEW_TICKET_TYPE and claims.get('site_id') == site_id


publish_service = PublishService()
