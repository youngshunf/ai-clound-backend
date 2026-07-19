"""HASN 资产业务服务（08/09 Stage1d-1f）。

职责：
- 注册资产（从 StorageService.upload 的 ObjectRef 落 hasn_assets，分配 asset_id）。
- 落消息时为私有附件按会话写 grant（关闭 08 §1.6 跨 owner 越权洞）。
- resolve：批量 asset_ids + 会话上下文 → 鉴权(三态) → public 直读 / private 签名 → display_url + expires_at。

鉴权（1f）：requester 是资产 owner，或（资产已授予该会话 且 requester 是该会话参与者），否则不可读。
零 fake：资产不存在/无权读 → 不返回 URL（调用方据缺失项报 403/隐藏），绝不伪造可读链接。
"""

from __future__ import annotations

import hashlib

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnAgents, HasnAssetGrants, HasnAssets, HasnConversations
from backend.app.hasn.model.hasn_conversation_memberships import (
    HasnConversationMemberships as HasnGroupMembers,
)
from backend.common.exception import errors
from backend.plugin.s3.model import S3Storage
from backend.plugin.s3.service.storage_service import ObjectRef, StorageService
from backend.utils.timezone import timezone


@dataclass(frozen=True)
class ResolvedAsset:
    asset_id: str
    display_url: str
    expires_at: str | None  # ISO8601；public 为 None（不过期）


class HasnAssetService:
    _LOCAL_SNAPSHOT_MAX_SIZE = {
        'image': 50 * 1024 * 1024,
        'voice': 25 * 1024 * 1024,
        'file': 1024 * 1024 * 1024,
    }

    @staticmethod
    def gen_asset_id() -> str:
        """asset_id：'ast_' + uuid4 hex（36 字符，落 varchar(40)）。"""
        return f'ast_{uuid4().hex}'

    @classmethod
    async def register_asset(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        ref: ObjectRef,
        kind: str,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
        transcript: str | None = None,
        thumbnail_asset_id: str | None = None,
        extract_status: str = 'pending',
        content_sha256: str | None = None,
    ) -> HasnAssets:
        """从 ObjectRef 落 hasn_assets，返回已 flush 的记录（含 asset_id）。"""
        asset = HasnAssets(
            asset_id=cls.gen_asset_id(),
            owner_hasn_id=owner_hasn_id,
            access=ref.access,
            storage_id=ref.storage_id,
            object_key=ref.object_key,
            kind=kind,
            mime=ref.mime,
            size_bytes=ref.size,
            content_sha256=content_sha256,
            width=width,
            height=height,
            duration_ms=duration_ms,
            transcript=transcript,
            thumbnail_asset_id=thumbnail_asset_id,
            extract_status=extract_status,
        )
        db.add(asset)
        await db.flush()
        return asset

    @staticmethod
    def _kind_of_mime(mime: str) -> str:
        """从服务端收到的 MIME 归类资产类型。"""
        if mime.startswith('image/'):
            return 'image'
        if mime.startswith('audio/'):
            return 'voice'
        return 'file'

    @classmethod
    async def upload_local_source_snapshot(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        data: bytes,
        filename: str | None,
        content_type: str | None,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
    ) -> HasnAssets:
        """上传一次本地原件快照；同主人、类型与 sha256 的重试返回同一资产。

        同一幂等键先取得 PostgreSQL 事务级 advisory lock，再查重并上传，避免并发重试
        同时越过查询。对象 key 仅由 sha256 派生，数据库提交失败后的重试也只会覆盖同一对象。
        """
        if not data:
            raise errors.RequestError(msg='文件为空')

        mime = (content_type or 'application/octet-stream').strip().lower()
        kind = cls._kind_of_mime(mime)
        limit = cls._LOCAL_SNAPSHOT_MAX_SIZE[kind]
        if len(data) > limit:
            raise errors.RequestError(msg=f'本地原件快照不能超过 {limit // (1024 * 1024)}MB')

        content_sha256 = hashlib.sha256(data).hexdigest()
        lock_key = f'hasn_asset:{owner_hasn_id}:{kind}:{content_sha256}'
        await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))

        existing = (
            await db.execute(
                select(HasnAssets)
                .where(
                    HasnAssets.owner_hasn_id == owner_hasn_id,
                    HasnAssets.kind == kind,
                    HasnAssets.content_sha256 == content_sha256,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        object_key = f'local-source-snapshots/{content_sha256[:2]}/{content_sha256}'
        ref = await StorageService.upload(
            db,
            data,
            category='local_source_snapshot',
            filename=filename,
            content_type=mime,
            key=object_key,
        )
        return await cls.register_asset(
            db,
            owner_hasn_id=owner_hasn_id,
            ref=ref,
            kind=kind,
            width=width,
            height=height,
            duration_ms=duration_ms,
            extract_status='done',
            content_sha256=content_sha256,
        )

    @staticmethod
    async def get_by_asset_id(db: AsyncSession, asset_id: str) -> HasnAssets | None:
        result = await db.execute(select(HasnAssets).where(HasnAssets.asset_id == asset_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_many(db: AsyncSession, asset_ids: list[str]) -> dict[str, HasnAssets]:
        if not asset_ids:
            return {}
        result = await db.execute(select(HasnAssets).where(HasnAssets.asset_id.in_(asset_ids)))
        return {a.asset_id: a for a in result.scalars().all()}

    @staticmethod
    async def grant_to_conversation(db: AsyncSession, *, asset_id: str, conversation_id: str | UUID) -> None:
        """幂等写 grant（UNIQUE(asset_id, conversation_id) 冲突即忽略）。"""
        stmt = (
            pg_insert(HasnAssetGrants)
            .values(asset_id=asset_id, conversation_id=conversation_id)
            .on_conflict_do_nothing(index_elements=['asset_id', 'conversation_id'])
        )
        await db.execute(stmt)

    @staticmethod
    async def _granted_asset_ids(db: AsyncSession, *, asset_ids: list[str], conversation_id: str | UUID) -> set[str]:
        if not asset_ids:
            return set()
        result = await db.execute(
            select(HasnAssetGrants.asset_id).where(
                HasnAssetGrants.asset_id.in_(asset_ids),
                HasnAssetGrants.conversation_id == conversation_id,
            )
        )
        return set(result.scalars().all())

    @staticmethod
    async def is_participant(db: AsyncSession, *, conversation_id: str | UUID, hasn_id: str) -> bool:
        """requester 是否该会话参与者（单聊 a/b、群成员，或**参与 agent 的主人**）。

        主人扩展（owner 透明原则，2026-07-05）：A2A 会话的参与者是两个 agent，但接收方
        daemon 一律以主人 owner JWT 解析附件（入站派发物化 / IM 下载）。主人天然旁观自己
        分身的会话，故「requester 是某参与 agent 的 owner」同样判参与者。资产面不放宽：
        resolve 仍要求资产已 grant 给该会话，本判定只解决"分身会话中主人代分身取附件"。
        """
        conv = await db.get(HasnConversations, conversation_id)
        if conv is None:
            return False
        participants = [p for p in (conv.participant_a_id, conv.participant_b_id) if p]
        if hasn_id in participants:
            return True
        if conv.type == 'group':
            result = await db.execute(
                select(HasnGroupMembers.member_id).where(
                    HasnGroupMembers.conversation_id == conversation_id,
                    HasnGroupMembers.left_seq.is_(None),
                    HasnGroupMembers.state == 'active',
                )
            )
            members = list(result.scalars().all())
            if hasn_id in members:
                return True
            participants = members
        agent_ids = [p for p in participants if p.startswith('a_')]
        if not agent_ids:
            return False
        owned = await db.execute(
            select(HasnAgents.id)
            .where(HasnAgents.hasn_id.in_(agent_ids), HasnAgents.owner_id == hasn_id)
            .limit(1)
        )
        return owned.first() is not None

    @classmethod
    async def resolve(
        cls,
        db: AsyncSession,
        *,
        requester_hasn_id: str,
        asset_ids: list[str],
        conversation_id: str | UUID | None = None,
        expires_in: int = 3600,
        extra_readable_asset_ids: set[str] | None = None,
    ) -> list[ResolvedAsset]:
        """批量解析为可展示 URL。无权/不存在的 asset 不出现在结果（调用方据此 403/隐藏）。

        extra_readable_asset_ids：资源上下文（如 deck ACL）已在**上游**判定 requester 有权看到的
        asset_id 集合，本方法据此额外放行——用于「被分享者打开共享 deck，其页内图片既非 owner
        也无会话授权」的场景。上游必须只塞入「requester 确有 viewer+ 权限的那个资源所引用的资产」，
        绝不能塞任意 asset（否则越权签发）。
        """
        assets = await cls.get_many(db, asset_ids)
        if not assets:
            return []

        # 一次性算：该会话被授予了哪些 asset + requester 是否参与该会话（避免逐个查）
        granted: set[str] = set()
        participant = False
        if conversation_id is not None:
            granted = await cls._granted_asset_ids(db, asset_ids=list(assets), conversation_id=conversation_id)
            participant = await cls.is_participant(db, conversation_id=conversation_id, hasn_id=requester_hasn_id)

        extra_readable = extra_readable_asset_ids or set()
        readable: list[HasnAssets] = []
        for asset in assets.values():
            if asset.access == 'public':
                readable.append(asset)  # public 恒可读（07 D3：公开直读，无需授权）
            elif requester_hasn_id == asset.owner_hasn_id:
                readable.append(asset)  # owner 恒可读
            elif participant and asset.asset_id in granted:
                readable.append(asset)  # 会话参与者 + 已授予
            elif asset.asset_id in extra_readable:
                readable.append(asset)  # 上游资源 ACL 已判定可读（如 deck viewer 打开共享 deck）
            # 否则（私有且无权）跳过（零 fake：不返回 URL）

        # public 直读、private 批量签名（缓存）
        results: list[ResolvedAsset] = []
        private_items: list[tuple[int, str]] = []
        private_assets: list[HasnAssets] = []
        storages_cache: dict[int, S3Storage] = {}
        for asset in readable:
            if asset.access == 'public':
                storage = storages_cache.get(asset.storage_id)
                if storage is None:
                    storage = await StorageService.get_storage(db, asset.storage_id)
                    storages_cache[asset.storage_id] = storage
                results.append(
                    ResolvedAsset(asset.asset_id, StorageService.public_url(storage, asset.object_key), None)
                )
            else:
                private_items.append((asset.storage_id, asset.object_key))
                private_assets.append(asset)

        if private_items:
            signed = await StorageService.signed_urls_cached(db, items=private_items, expires_in=expires_in)
            expires_at = (timezone.now() + timedelta(seconds=expires_in)).isoformat()
            for asset in private_assets:
                url = signed.get((asset.storage_id, asset.object_key))
                if url:
                    results.append(ResolvedAsset(asset.asset_id, url, expires_at))
        return results


hasn_asset_service: HasnAssetService = HasnAssetService()
