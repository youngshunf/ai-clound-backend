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

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update

from backend.app.hasn.model import HasnAgents, HasnArtifacts
from backend.app.hasn.schema.hasn_artifacts import (
    ArtifactDetail,
    ArtifactItem,
    RecordArtifactParam,
)
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.schema.resource_descriptor import ResourceDescriptor

# 允许的产物类型（白名单，越界归一为 other）。
_ALLOWED_KINDS = {'image', 'voice', 'video', 'file', 'document', 'deck', 'webpage', 'dataset', 'other'}
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
        # 产物本体三选一：body（文本/markdown 直接入库）/ asset_id（二进制）/ resource_uri（hasn:// 资源）。
        # 文本产物走 body 不上传文件（P6）。
        if not params.asset_id and not params.resource_uri and not params.body:
            raise errors.RequestError(msg='产物必须带 body、asset_id 或 resource_uri 其一')

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
            body=(params.body or None),
            asset_id=(params.asset_id or None),
            resource_uri=(params.resource_uri or None),
            origin_ref=(params.origin_ref or None),
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

    @staticmethod
    def _app_id_from_descriptor(descriptor: ResourceDescriptor) -> str:
        """从 `descriptor.resource_kind`（约定 `{app}.{kind}`，如 deck.presentation）取 app_id。"""
        resource_kind = (descriptor.resource_kind or '').strip()
        return resource_kind.split('.', 1)[0] if resource_kind else 'app'

    @classmethod
    def _resolve_artifact_kind(cls, descriptor: ResourceDescriptor) -> str:
        """产物 kind：优先 `descriptor.artifact_kind`，缺省按 `resource_kind` 尾段归一，越界 → other。"""
        declared = (descriptor.artifact_kind or '').strip()
        if declared:
            return declared if declared in _ALLOWED_KINDS else 'other'
        tail = (descriptor.resource_kind or '').rsplit('.', 1)[-1].strip()
        return tail if tail in _ALLOWED_KINDS else 'other'

    @classmethod
    async def record_app_resource_artifact(
        cls,
        db: AsyncSession,
        *,
        descriptor: ResourceDescriptor,
        server_id: str,
        session_id: str | None,
        agent_hasn_id: str,
        owner_hasn_id: str,
        title: str,
        summary: str | None = None,
        source_tool: str | None = None,
        dispatch_id: str | None = None,
    ) -> str:
        """据 descriptor 登记一条**应用资源产物**（deck/webpage 等，走 `resource_uri` 指针，无 asset 本体）。

        RC-P8：完成卡投影（`_projection_card_body`）的**同处**调用，让分身产出的 deck/网站/短视频等
        应用资源自动登记进 `hasn_artifacts`，从而出现在「工作会话资源栏 / 分身产物 tab」。返回 artifact_id。

        - `resource_uri = hasn://{descriptor.uri_domain}/{server_id}`——`server_id` 必须是**云端权威 id**
          （调用方已优先取 `{app}_server_id`，未上云才回退 local_ref）；跨设备/分享后对端据云端 id 打开
          （Core-08 URI 第二原则：本地 id 永不上 URI）。
        - `kind = descriptor.artifact_kind`（缺省按 `resource_kind` 尾段归一，越界 → other）；
          `source_kind='tool_output'`。
        - `dispatch_id` 幂等（缺省 `f"{app_id}:{server_id}"`）：应用资源无 asset_id，
          按 `(agent, dispatch_id, resource_uri)` 查既有 active 行，重复投影同一资源不重复登记。
        """
        app_id = cls._app_id_from_descriptor(descriptor)
        resource_uri = f'hasn://{descriptor.uri_domain}/{server_id}'
        kind = cls._resolve_artifact_kind(descriptor)
        effective_dispatch_id = dispatch_id or f'{app_id}:{server_id}'

        # UPSERT：应用资源无 asset_id（record 的 dispatch_id+asset_id 去重键不适用），按
        # (agent, dispatch_id, resource_uri) 查既有 active 行。命中即**就地推进**（register-on-write
        # 语义）——同一 deck/app 资源被分身反复写（create→逐页写→finalize，或主人手建后分身改），
        # 每次都调本函数，第一次插入、后续推进，绝不重复登记也绝不丢失会话归属：
        #   · session_id 只进不退：新 session 非空则采纳，为空则保留原值（工作会话写点先带上 id，
        #     完成卡投影后补的 session_id=None 不得把它降级为 None）；
        #   · title/summary 有更佳值（非空且不同）则刷新；updated_time 由 onupdate 自动刷新。
        existing_row = (
            await db.execute(
                select(HasnArtifacts)
                .where(
                    HasnArtifacts.agent_hasn_id == agent_hasn_id,
                    HasnArtifacts.dispatch_id == effective_dispatch_id,
                    HasnArtifacts.resource_uri == resource_uri,
                    HasnArtifacts.status == 'active',
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_row is not None:
            changed = False
            if session_id and existing_row.session_id != session_id:
                existing_row.session_id = session_id
                changed = True
            new_title = title or None
            if new_title and existing_row.title != new_title:
                existing_row.title = new_title
                changed = True
            new_summary = summary or None
            if new_summary and existing_row.summary != new_summary:
                existing_row.summary = new_summary
                changed = True
            if changed:
                await db.flush()
            return existing_row.artifact_id

        artifact_id = cls.gen_artifact_id()
        row = HasnArtifacts(
            artifact_id=artifact_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            kind=kind,
            title=(title or None),
            summary=(summary or None),
            resource_uri=resource_uri,
            # origin_ref 存**云端权威** resource:{app}:{server_id}（不存本地 id），按业务对象可反查产物。
            origin_ref=f'resource:{app_id}:{server_id}',
            session_id=(session_id or None),
            source_tool=(source_tool or None),
            source_kind='tool_output',
            dispatch_id=effective_dispatch_id,
            meta_data={},
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
            body=row.body,
            asset_id=row.asset_id,
            resource_uri=row.resource_uri,
            origin_ref=row.origin_ref,
            conversation_id=str(row.conversation_id) if row.conversation_id else None,
            message_id=row.message_id,
            session_id=row.session_id,
            source_tool=row.source_tool,
            source_kind=row.source_kind,
            source_link=cls.derive_source_link(row.conversation_id, row.message_id, row.session_id),
            display_url=url_map.get(row.asset_id) if row.asset_id else None,
            created_time=row.created_time,
        )

    @staticmethod
    def _ilike_pattern(word: str) -> str:
        """把关键词转义成安全的 ILIKE 子串模式：`\\` `%` `_` 逐个转义，两侧补 `%`。

        防通配符意外全量匹配（分身传 `%` 不该匹配全部产物）。配 `escape='\\'` 使用。
        """
        escaped = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        return f'%{escaped}%'

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
        keyword: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[ArtifactItem], int]:
        """列某分身的产物（时间线倒序）。先校验归属，再批量解析图片缩略图。

        可选过滤（向后兼容，缺省不生效）：
        - `keyword`：按空白切词，每词做 ILIKE 转义后 OR 命中 title/summary，**词间 AND**
          （「星空 城市」= 两词都命中）——图文取材子串检索（`hasn.artifact.search`）。
        - `session_id`：只看某个工作会话产出的（「找我这个任务里产的东西」）。
        """
        if not await cls._owns_agent(db, owner_hasn_id=owner_hasn_id, agent_hasn_id=agent_hasn_id):
            raise errors.ForbiddenError(msg='无权查看该分身的产物')

        conds = [
            HasnArtifacts.owner_hasn_id == owner_hasn_id,
            HasnArtifacts.agent_hasn_id == agent_hasn_id,
            HasnArtifacts.status == 'active',
        ]
        if kind:
            conds.append(HasnArtifacts.kind == kind)
        if session_id:
            conds.append(HasnArtifacts.session_id == session_id)
        if keyword:
            # 切词：每词 OR 命中 title/summary，词间 AND（全部命中才算匹配）。空词跳过。
            for word in keyword.split():
                pattern = cls._ilike_pattern(word)
                conds.append(
                    or_(
                        HasnArtifacts.title.ilike(pattern, escape='\\'),
                        HasnArtifacts.summary.ilike(pattern, escape='\\'),
                    )
                )

        total = (await db.execute(select(func.count()).select_from(HasnArtifacts).where(*conds))).scalar_one()
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
        total = (await db.execute(select(func.count()).select_from(HasnArtifacts).where(*conds))).scalar_one()
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
    async def list_by_origin(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        origin_ref: str,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[ArtifactItem], int]:
        """列某业务对象（origin_ref，如 resource:plan:todo:{id}）产出的产物（时间线倒序）。

        owner 隔离：仅返回本 owner 名下产物。规划详情产物轨（P6-C）按此反查。
        """
        conds = [
            HasnArtifacts.owner_hasn_id == owner_hasn_id,
            HasnArtifacts.origin_ref == origin_ref,
            HasnArtifacts.status == 'active',
        ]
        total = (await db.execute(select(func.count()).select_from(HasnArtifacts).where(*conds))).scalar_one()
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
    async def list_by_session(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        session_id: str,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[ArtifactItem], int]:
        """列某工作会话（session_id）产出的产物（时间线倒序）。

        RC-P4「工作会话页资源栏」：分身在某工作会话产出的 deck/网站/短视频等应用资源（经 RC-P8
        `record_app_resource_artifact` 登记时带上会话 session_id）+ 工具产出产物，据 session_id 反查。
        `session_id` 语义 = hasn-node 本地工作会话 id（daemon 投影完成卡与工具捕获时同一 id·V1 已核），
        与 webui 工作会话页 `/apps/tasks/sessions/:id` 的 id 一致。owner 隔离：仅返回本 owner 名下产物。
        """
        conds = [
            HasnArtifacts.owner_hasn_id == owner_hasn_id,
            HasnArtifacts.session_id == session_id,
            HasnArtifacts.status == 'active',
        ]
        total = (await db.execute(select(func.count()).select_from(HasnArtifacts).where(*conds))).scalar_one()
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
    async def get_detail(cls, db: AsyncSession, *, owner_hasn_id: str, artifact_id: str) -> ArtifactDetail:
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
            await cls._resolve_urls(db, owner_hasn_id=owner_hasn_id, asset_ids=[row.asset_id]) if row.asset_id else {}
        )
        signed = url_map.get(row.asset_id) if row.asset_id else None
        item = cls._to_item(row, url_map)
        return ArtifactDetail(
            **item.model_dump(),
            download_url=signed,
            metadata=row.meta_data or {},
        )

    @classmethod
    async def update_content(
        cls, db: AsyncSession, *, owner_hasn_id: str, artifact_id: str, body: str, title: str | None = None
    ) -> None:
        """Owner 更新产物正文（markdown 编辑保存）。仅本 owner 的 active 行可改。

        只写 body（+可选 title），不动 asset_id/resource_uri 指针——asset 型 .md 产物编辑后
        body 成为权威正文（前端渲染 body 优先），原文件仍可下载。
        """
        values: dict = {'body': body}
        if title is not None and title.strip():
            values['title'] = title.strip()
        result = await db.execute(
            update(HasnArtifacts)
            .where(
                HasnArtifacts.artifact_id == artifact_id,
                HasnArtifacts.owner_hasn_id == owner_hasn_id,
                HasnArtifacts.status == 'active',
            )
            .values(**values)
        )
        if result.rowcount == 0:
            raise errors.NotFoundError(msg='产物不存在或无权修改')

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
