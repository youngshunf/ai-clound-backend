"""分身产物（Artifacts）业务服务（AF-2）。

职责：
- record：Agent 登记一条产物（身份由调用方传入，取自 Agent JWT；去重幂等）。
- list_by_agent：Owner 查某分身的产物时间线（先校验分身归属本人；批量 resolve_assets 出缩略图）。
- get_detail：单条产物详情（display_url + download_url 经 resolve_assets 签名）。
- soft_delete：软删指针（status='deleted'，不删 asset 本体）。

设计：docs/Agent产物系统/00-Agent产物存储与展示下载设计.md §5/§6/§8。
零拷贝：产物只持 asset_id/resource_uri 指针；展示时 asset_id 经 resolve_assets 换签名 URL，绝不存 CDN 直链。
零 fake：无权/不存在 → 抛错或留空，绝不伪造可读链接。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnAgents, HasnArtifacts
from backend.app.hasn.schema.hasn_artifacts import (
    ArtifactDetail,
    ArtifactItem,
    RecordArtifactParam,
)
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.common.exception import errors

# 允许的产物类型（白名单，越界归一为 other）。
_ALLOWED_KINDS = {'image', 'voice', 'file', 'document', 'deck', 'webpage', 'dataset', 'other'}
_ALLOWED_SOURCE_KINDS = {'tool_output', 'task_result', 'upload', 'external'}


class HasnArtifactsService:
    @staticmethod
    def gen_artifact_id() -> str:
        """artifact_id：'art_' + uuid4 hex（36 字符，落 varchar(40)）。"""
        return f'art_{uuid4().hex}'

    @staticmethod
    def _coerce_uuid(value: str | UUID | None) -> UUID | None:
        """会话 ID 软校验：合法 UUID 才落库，否则留空（best-effort 捕获不因脏 conv 失败）。"""
        if value is None:
            return None
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return None

    @staticmethod
    def derive_source_link(
        conversation_id: str | UUID | None,
        message_id: int | None,
        session_id: str | None,
    ) -> str | None:
        """跳转主锚（D4）：有会话+消息 → 精确到消息气泡；仅 session → 降级任务 session。

        客户端无关 hasn:// URI（host=资源域），见 Core/08 资源寻址规范。
        """
        if conversation_id:
            conv = str(conversation_id)
            if message_id:
                return f'hasn://messages/c/{conv}#{message_id}'
            return f'hasn://messages/c/{conv}'
        if session_id:
            return f'hasn://tasks/sessions/{session_id}'
        return None

    @classmethod
    async def _owns_agent(cls, db: AsyncSession, *, owner_hasn_id: str, agent_hasn_id: str) -> bool:
        """校验该分身归属本 owner（远端/他人分身不可查，与 AgentIdentity 诚实留空一致）。"""
        found = (
            await db.execute(
                select(HasnAgents.hasn_id)
                .where(HasnAgents.hasn_id == agent_hasn_id, HasnAgents.owner_id == owner_hasn_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        return found is not None

    @classmethod
    async def record(
        cls,
        db: AsyncSession,
        *,
        agent_hasn_id: str,
        owner_hasn_id: str,
        params: RecordArtifactParam,
    ) -> str:
        """登记一条产物，返回 artifact_id。去重键 (agent, dispatch_id, asset_id) 命中则返回既有 id。

        身份由调用方注入（取自 Agent JWT），绝不信任 body 里的 agent/owner。
        """
        if not params.asset_id and not params.resource_uri:
            raise errors.RequestError(msg='产物必须带 asset_id 或 resource_uri 其一')

        kind = params.kind if params.kind in _ALLOWED_KINDS else 'other'
        source_kind = params.source_kind if params.source_kind in _ALLOWED_SOURCE_KINDS else 'tool_output'
        conv = cls._coerce_uuid(params.conversation_id)

        # 去重（重试幂等）：仅当 dispatch_id + asset_id 都在时按去重键查既有 active 记录。
        if params.dispatch_id and params.asset_id:
            existing = (
                await db.execute(
                    select(HasnArtifacts.artifact_id)
                    .where(
                        HasnArtifacts.agent_hasn_id == agent_hasn_id,
                        HasnArtifacts.dispatch_id == params.dispatch_id,
                        HasnArtifacts.asset_id == params.asset_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing:
                return existing

        artifact_id = cls.gen_artifact_id()
        row = HasnArtifacts(
            artifact_id=artifact_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            kind=kind,
            title=(params.title or None),
            summary=(params.summary or None),
            asset_id=(params.asset_id or None),
            resource_uri=(params.resource_uri or None),
            conversation_id=conv,
            message_id=params.message_id,
            session_id=(params.session_id or None),
            source_tool=(params.source_tool or None),
            source_kind=source_kind,
            dispatch_id=(params.dispatch_id or None),
            meta_data=params.metadata or {},
            status='active',
        )
        db.add(row)
        await db.flush()
        return artifact_id

    @classmethod
    async def _resolve_urls(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        asset_ids: list[str],
    ) -> dict[str, str]:
        """批量把 asset_id 换成 owner 可读的签名 URL（owner 恒可读自己资产，无需会话授权）。"""
        ids = [a for a in dict.fromkeys(asset_ids) if a]
        if not ids:
            return {}
        resolved = await hasn_asset_service.resolve(
            db, requester_hasn_id=owner_hasn_id, asset_ids=ids, conversation_id=None
        )
        return {r.asset_id: r.display_url for r in resolved}

    @classmethod
    def _to_item(cls, row: HasnArtifacts, url_map: dict[str, str]) -> ArtifactItem:
        return ArtifactItem(
            artifact_id=row.artifact_id,
            kind=row.kind,
            title=row.title,
            summary=row.summary,
            asset_id=row.asset_id,
            resource_uri=row.resource_uri,
            conversation_id=str(row.conversation_id) if row.conversation_id else None,
            message_id=row.message_id,
            session_id=row.session_id,
            source_tool=row.source_tool,
            source_kind=row.source_kind,
            source_link=cls.derive_source_link(row.conversation_id, row.message_id, row.session_id),
            display_url=url_map.get(row.asset_id) if row.asset_id else None,
            created_time=row.created_time,
        )

    @classmethod
    async def list_by_agent(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
        page: int = 1,
        size: int = 20,
        kind: str | None = None,
    ) -> tuple[list[ArtifactItem], int]:
        """列某分身的产物（时间线倒序）。先校验归属，再批量解析图片缩略图。"""
        if not await cls._owns_agent(db, owner_hasn_id=owner_hasn_id, agent_hasn_id=agent_hasn_id):
            raise errors.ForbiddenError(msg='无权查看该分身的产物')

        conds = [
            HasnArtifacts.owner_hasn_id == owner_hasn_id,
            HasnArtifacts.agent_hasn_id == agent_hasn_id,
            HasnArtifacts.status == 'active',
        ]
        if kind:
            conds.append(HasnArtifacts.kind == kind)

        total = (
            await db.execute(select(func.count()).select_from(HasnArtifacts).where(*conds))
        ).scalar_one()
        rows = (
            (
                await db.execute(
                    select(HasnArtifacts)
                    .where(*conds)
                    .order_by(HasnArtifacts.created_time.desc(), HasnArtifacts.id.desc())
                    .offset(max(0, (page - 1) * size))
                    .limit(size)
                )
            )
            .scalars()
            .all()
        )
        url_map = await cls._resolve_urls(
            db, owner_hasn_id=owner_hasn_id, asset_ids=[r.asset_id for r in rows if r.asset_id]
        )
        return [cls._to_item(r, url_map) for r in rows], total

    @classmethod
    async def list_for_owner(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        page: int = 1,
        size: int = 20,
        kind: str | None = None,
    ) -> tuple[list[ArtifactItem], int]:
        """聚合时间线：owner 名下全部分身的产物。"""
        conds = [HasnArtifacts.owner_hasn_id == owner_hasn_id, HasnArtifacts.status == 'active']
        if kind:
            conds.append(HasnArtifacts.kind == kind)
        total = (
            await db.execute(select(func.count()).select_from(HasnArtifacts).where(*conds))
        ).scalar_one()
        rows = (
            (
                await db.execute(
                    select(HasnArtifacts)
                    .where(*conds)
                    .order_by(HasnArtifacts.created_time.desc(), HasnArtifacts.id.desc())
                    .offset(max(0, (page - 1) * size))
                    .limit(size)
                )
            )
            .scalars()
            .all()
        )
        url_map = await cls._resolve_urls(
            db, owner_hasn_id=owner_hasn_id, asset_ids=[r.asset_id for r in rows if r.asset_id]
        )
        return [cls._to_item(r, url_map) for r in rows], total

    @classmethod
    async def get_detail(
        cls, db: AsyncSession, *, owner_hasn_id: str, artifact_id: str
    ) -> ArtifactDetail:
        """单条产物详情（含 display_url + download_url）。仅本 owner 可读。"""
        row = (
            await db.execute(
                select(HasnArtifacts)
                .where(
                    HasnArtifacts.artifact_id == artifact_id,
                    HasnArtifacts.owner_hasn_id == owner_hasn_id,
                    HasnArtifacts.status == 'active',
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if not row:
            raise errors.NotFoundError(msg='产物不存在或无权访问')

        url_map = (
            await cls._resolve_urls(db, owner_hasn_id=owner_hasn_id, asset_ids=[row.asset_id])
            if row.asset_id
            else {}
        )
        signed = url_map.get(row.asset_id) if row.asset_id else None
        item = cls._to_item(row, url_map)
        return ArtifactDetail(
            **item.model_dump(),
            download_url=signed,
            metadata=row.meta_data or {},
        )

    @classmethod
    async def soft_delete(cls, db: AsyncSession, *, owner_hasn_id: str, artifact_id: str) -> None:
        """软删指针（不删 asset 本体——asset 可能仍被消息引用）。"""
        result = await db.execute(
            update(HasnArtifacts)
            .where(
                HasnArtifacts.artifact_id == artifact_id,
                HasnArtifacts.owner_hasn_id == owner_hasn_id,
                HasnArtifacts.status == 'active',
            )
            .values(status='deleted')
        )
        if result.rowcount == 0:
            raise errors.NotFoundError(msg='产物不存在或无权删除')


hasn_artifacts_service: HasnArtifactsService = HasnArtifactsService()
