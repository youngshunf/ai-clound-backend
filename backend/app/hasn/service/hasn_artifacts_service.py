"""分身产物（Artifacts）业务服务（AF-2）。

职责：
- record：将旧 Agent 写入入口适配为统一登记服务。
- list_*：将历史调用方适配为统一产物查询 DTO。
- get_detail：在统一查询结果上补齐完整正文和元数据。
- soft_delete：软删指针（status='deleted'，不删 asset 本体）。

设计：docs/Agent产物系统/00-Agent产物存储与展示下载设计.md §5/§6/§8。
零拷贝：产物只持 asset_id/resource_uri 指针；统一查询时解析可展示资产，绝不存 CDN 直链。
零 fake：无权/不存在 → 抛错或留空，绝不伪造可读链接。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

from sqlalchemy import select, update

from backend.app.hasn.model import HasnAgents, HasnArtifacts, HasnSessions
from backend.app.hasn.schema.artifact_contract import ArtifactListItem, ArtifactMutation
from backend.app.hasn.schema.hasn_artifacts import (
    ArtifactDetail,
    ArtifactItem,
    RecordArtifactParam,
)
from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
from backend.app.hasn.service.artifact_query_service import artifact_query_service
from backend.app.hasn.service.artifact_registration_service import artifact_registration_service
from backend.common.exception import errors
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.schema.resource_descriptor import ResourceDescriptor


async def coalesce_legacy_work_session_id(
    db: AsyncSession,
    *,
    owner_hasn_id: str,
    session_id: str,
) -> str | None:
    """过渡兼容：旧节点只发混合语义的 `session_id`，按「确实在册的工作会话」收窄回落。

    旧节点工作会话派发发真实工作会话 id、主会话派发发运行时逻辑会话 id，两者同形无法
    从值区分；但运行时/interactive 会话**不是** `hasn_sessions` 的 task 行，主会话派发的
    值在这里查无 → 绝不进工作会话列（设计 §4.3：`work_session_id` 只接受工作会话 ID）。
    旧节点的工作会话派发不受此限——其 session_id 本就是在册 task 会话，照常回落绑上。

    模块级公共函数：产物登记（`HasnArtifactsService.record`）与两个 MCP 分发入口
    （`mcp/server.py`、`ai_native_runtime_gateway.py` 的 ContextVar 回填）共用同一收窄判据。
    """
    found = (
        await db.execute(
            select(HasnSessions.session_id)
            .where(
                HasnSessions.session_id == session_id,
                HasnSessions.owner_id == owner_hasn_id,
                HasnSessions.session_kind == 'task',
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if found is None:
        log.warning(
            '产物登记的 session_id 不是在册工作会话，按运行时溯源处理：owner=%s session_id=%s',
            owner_hasn_id,
            session_id,
        )
    return found


class HasnArtifactsService:
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
        # 工作会话轴与运行时 session 分流（设计 §4.3）：显式 `work_session_id` 直接采信；
        # 缺省时 `session_id` 只在确为在册工作会话（task）时才回落——旧节点主会话派发灌进来的
        # 运行时逻辑会话 id 从此只能进 metadata 溯源，不再污染工作会话列。
        work_session_id = params.work_session_id
        if work_session_id is None and params.session_id:
            work_session_id = await coalesce_legacy_work_session_id(
                db,
                owner_hasn_id=owner_hasn_id,
                session_id=params.session_id,
            )
        metadata = dict(params.metadata)
        if params.session_id and params.session_id != work_session_id:
            # 运行时 session 是溯源元数据，不是工作会话——进 metadata，绝不占工作会话列。
            metadata.setdefault('runtime_session_id', params.session_id)
        try:
            mutation = ArtifactMutation(
                owner_hasn_id=owner_hasn_id,
                agent_hasn_id=agent_hasn_id,
                action=params.action,
                source_kind=params.source_kind,
                artifact_kind=params.kind,
                body=params.body,
                asset_id=params.asset_id,
                resource_uri=params.resource_uri,
                source_asset_uri=params.source_asset_uri,
                source_hash=params.source_hash,
                source_synced_at=params.source_synced_at,
                resource_kind=params.resource_kind,
                resource_app_id=params.source_app_id,
                origin_ref=params.origin_ref,
                local_locator_key=params.local_locator_key,
                local_entry_kind=params.local_entry_kind,
                node_id=params.node_id,
                work_session_id=work_session_id,
                project_id=params.project_id,
                conversation_id=params.conversation_id,
                message_id=params.message_id,
                source_tool=params.source_tool,
                source_app_id=params.source_app_id,
                dispatch_id=params.dispatch_id,
                tool_call_id=params.tool_call_id,
                # `origin_ref` 是「产出所属业务资源」的反查指针（第 96 行单列），不是产出事件身份；
                # 线上契约根本没有 source_event_id 入参，不得把 origin_ref 复制进去——否则同一
                # 业务资源下的正文产物会共享 `body:{app}:{origin_ref}` 对象键互相覆盖，参与记录
                # 也会沿 `event:{origin_ref}:...` 兜底键折叠。
                source_event_id=None,
                idempotency_key=params.idempotency_key,
                supersedes_locator_key=params.supersedes_locator_key,
                title=params.title,
                summary=params.summary,
                metadata=metadata,
            )
        except ValueError as exc:
            raise errors.RequestError(msg=f'产物登记参数不符合第一阶段契约：{exc}') from exc

        return (await artifact_registration_service.register(db, mutation)).artifact_id

    @staticmethod
    def _app_id_from_descriptor(descriptor: ResourceDescriptor) -> str:
        """从 `descriptor.resource_kind`（约定 `{app}.{kind}`，如 deck.presentation）取 app_id。"""
        resource_kind = (descriptor.resource_kind or '').strip()
        return resource_kind.split('.', 1)[0] if resource_kind else 'app'

    @classmethod
    def _resolve_artifact_kind(cls, descriptor: ResourceDescriptor) -> str:
        """应用资源产物的 kind 恒为 `resource`（doc35 §3）。

        17 条 descriptor 全是 AI-Native 应用资源——都有 `uri_domain`、都产 `hasn://` URI、
        UI 打开行为完全一致（据 URI 域跳应用）。它们本就同族，「是什么」由 `resource_kind`
        回答、「哪个应用」由 `source_app_id` 回答，`artifact_kind` 不该再掺和进来。

        旧实现「按 resource_kind 尾段归一 + 越界→other」正是把 `studio.project` 谎报成
        `video`、`design.project` 谎报成 `image` 的地方——一个**项目**不是媒体文件，
        若 UI 真按 kind 渲染就会给 `hasn://studio/projects/{id}` 挂个视频播放器（§1.2）。
        descriptor 的 `artifact_kind` 现已是 `Literal['resource'] | None`，这里保留声明优先
        只为兼容显式写 'resource' 的 manifest；缺省同样是 'resource'。
        """
        return (descriptor.artifact_kind or 'resource').strip() or 'resource'

    @staticmethod
    def _build_origin_ref(descriptor: ResourceDescriptor, *, app_id: str, server_id: str) -> str:
        """据 descriptor 派生 origin_ref——**全仓唯一拼接点**，调用方不得手拼补丁（05 §1.2 第 4 条）。

        `ref_type` 是 **opt-in** 的（`resolve_resource_descriptor` 的两种模式）：

        - **单资源应用**（deck/reel/design/knowledge，均未声明 `ref_type`）→ `resource:{app}:{id}`，
          解析时整段作 id。保持历史行为不动。
        - **多资源应用**（plan 的 goal/plan、finance 的 6 类）→ `resource:{app}:{ref_type}:{id}`。
          少了 `ref_type` 段，`resolve_resource_descriptor` 拿 `42` 去 partition 会返 `(None, None)`，
          完成卡就丢掉资源入口——这正是本函数以前硬编码单资源形状留下的坑。

        判据是「要不要按业务对象反查」：finance 要支持从策略/影子账户详情页派分身协作，
        那条链路靠 `work_session.origin_ref` 的 `ref_type` 段反查是哪类资源。
        """
        ref_type = (descriptor.ref_type or '').strip()
        if ref_type:
            return f'resource:{app_id}:{ref_type}:{server_id}'
        return f'resource:{app_id}:{server_id}'

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
        project_id: str | None = None,
        action: Literal['create', 'update'] = 'create',
        metadata: dict[str, object] | None = None,
        accumulate_metadata_keys: list[str] | None = None,
    ) -> ArtifactRegistration:
        """据 descriptor 登记一条**应用资源产物**（deck/webpage 等，走 `resource_uri` 指针，无 asset 本体）。

        RC-P8：完成卡投影（`_projection_card_body`）的**同处**调用，让分身产出的 deck/网站/短视频等
        应用资源自动登记进 `hasn_artifacts`，从而出现在「工作会话资源栏 / 分身产物 tab」。

        返回 `ArtifactRegistration(artifact_id, resource_uri)`（doc36 §3.1 D1）——以前只返 artifact_id，
        算好的 `resource_uri` 当场丢弃，于是**分身写完拿不到能打开的地址**（doc36 §1.4 的根因单点）。
        写工具把 `resource_uri` 放进返回体，分身就不必二次查询。

        - `resource_uri = hasn://{descriptor.uri_domain}/{server_id}`——`server_id` 必须是**云端权威 id**
          （调用方已优先取 `{app}_server_id`，未上云才回退 local_ref）；跨设备/分享后对端据云端 id 打开
          （Core-08 URI 第二原则：本地 id 永不上 URI）。
        - `kind='resource'` 恒定 + `resource_kind=descriptor.resource_kind` 原值 + `source_kind='app'`
          （doc35）。`resource_kind` 以前**被丢掉了**——知识库塌成 dataset、知识文档塌成 document，
          两者在 UI 上再也分不开，这就是信息丢失的根源（§4.2）。
        - `dispatch_id` 幂等（缺省 `f"{app_id}:{server_id}"`）：应用资源无 asset_id，
          按 `(agent, dispatch_id, resource_uri)` 查既有 active 行，重复投影同一资源不重复登记。
        """
        app_id = cls._app_id_from_descriptor(descriptor)
        # URI 一律经 descriptor.build_uri（全仓唯一拼接点，doc36 §3.1）——别在这里手拼字面量。
        resource_uri = descriptor.build_uri(server_id)
        kind = cast('Literal["resource"]', cls._resolve_artifact_kind(descriptor))
        effective_dispatch_id = dispatch_id or f'{app_id}:{server_id}'
        origin_ref = cls._build_origin_ref(descriptor, app_id=app_id, server_id=server_id)

        mutation = ArtifactMutation(
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            action=action,
            source_kind='app_write',
            artifact_kind=kind,
            resource_uri=resource_uri,
            resource_kind=descriptor.resource_kind,
            resource_app_id=app_id,
            origin_ref=origin_ref,
            work_session_id=session_id,
            project_id=project_id,
            source_tool=source_tool,
            source_app_id=app_id,
            dispatch_id=effective_dispatch_id,
            title=title or None,
            summary=summary or None,
            metadata={**(metadata or {}), 'origin_ref': origin_ref},
            accumulate_metadata_keys=accumulate_metadata_keys or [],
        )
        registered = await artifact_registration_service.register(db, mutation)
        assert registered.resource_uri is not None
        return ArtifactRegistration(
            artifact_id=registered.artifact_id,
            resource_uri=registered.resource_uri,
        )

    @staticmethod
    def _legacy_item(item: ArtifactListItem) -> ArtifactItem:
        """供尚未迁移的 MCP 调用方读取统一 DTO；HTTP API 已直接返回 ArtifactListItem。"""
        asset_id = item.asset_uri.rsplit('/', 1)[-1] if item.asset_uri else None
        return ArtifactItem(
            artifact_id=item.artifact_id,
            kind=item.artifact_kind,
            resource_kind=item.resource_kind,
            title=item.title,
            summary=item.summary,
            body=item.body_preview,
            asset_id=asset_id,
            resource_uri=item.resource_uri,
            source_asset_uri=item.source_asset_uri,
            source_hash=item.source_hash,
            source_synced_at=item.source_synced_at,
            local_path=None,
            node_id=item.local_entry.node_id if item.local_entry else None,
            origin_ref=None,
            # doc97：把「哪个分身产的」透出来——项目内跨分身查产物时，分身要据它判断这条是哪一环的产出。
            agent_hasn_id=item.latest_contribution.agent_hasn_id,
            conversation_id=None,
            message_id=None,
            session_id=item.latest_contribution.work_session_id,
            source_tool=item.latest_contribution.source_tool,
            source_app_id=item.latest_contribution.source_app_id,
            source_kind=item.latest_contribution.source_kind,
            action=item.latest_contribution.action,
            source_link=item.latest_contribution.source_link,
            display_url=item.preview_url,
            created_time=item.created_time,
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
        keyword: str | None = None,
        session_id: str | None = None,
        source_app_id: str | None = None,
        resource_kind: str | None = None,
        project_id: str | None = None,
    ) -> tuple[list[ArtifactItem], int]:
        """列某分身的产物（时间线倒序）。先校验归属，再批量解析图片缩略图。

        可选过滤（向后兼容，缺省不生效）：
        - `keyword`：按空白切词，每词做 ILIKE 转义后 OR 命中 title/summary，**词间 AND**
          （「星空 城市」= 两词都命中）——图文取材子串检索（`hasn.artifact.search`）。
        - `session_id`：只看某个工作会话产出的（「找我这个任务里产的东西」）。
        - `source_app_id` / `resource_kind`（doc36 U3）：**应用维度**过滤——「我在知识库里
          建过哪些库」。此前只能按 `kind` 筛，而应用资源的 `kind` 恒为 `resource`（doc35 四维
          分类），于是 18 个应用的资源全挤在同一个桶里、按 kind 筛等于没筛。应用维度的答案
          在 `source_app_id`（哪个应用）+ `resource_kind`（是什么）这两列上，本就已落库，
          只是查询面一直没开出去。
        - `project_id`（doc95 §6.4 项目轴）：只看某平台项目下产出的产物——分身在项目工作会话内
          收到「本项目全链路产物索引」后，用它把 list/search 收窄到本项目。`project_id` 是聚合
          过滤键、**不是权限边界**（归属仍由 owner+agent 隔离兜底）。
        """
        if not await cls._owns_agent(db, owner_hasn_id=owner_hasn_id, agent_hasn_id=agent_hasn_id):
            raise errors.ForbiddenError(msg='无权查看该分身的产物')

        result = await artifact_query_service.list(
            db,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            work_session_id=session_id,
            project_id=project_id,
            artifact_kind=kind,
            source_app_id=source_app_id,
            resource_kind=resource_kind,
            keyword=keyword,
            page=page,
            size=size,
        )
        return [cls._legacy_item(item) for item in result.items], result.total

    @classmethod
    async def list_in_project(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        project_id: str,
        page: int = 1,
        size: int = 20,
        kind: str | None = None,
        keyword: str | None = None,
        session_id: str | None = None,
        source_app_id: str | None = None,
        resource_kind: str | None = None,
    ) -> tuple[list[ArtifactItem], int]:
        """列**某平台项目内全部分身**的产物（doc97 T1-A：场景多环跨分身取上游产出）。

        与 [`list_by_agent`] 的唯一差别是**不按 `agent_hasn_id` 收窄**：场景工作流每一环
        通常是不同专家分身，第 2 环按 project_id 查若仍按调用分身隔离，就永远看不到第 1 环的
        产出，还会据此误判「项目里就这些」（doc95 §6.2-6 零 fake 的反面）。

        权限边界仍是 **owner 隔离**（`owner_hasn_id`），与 `hasn.artifact.get`「同主人任意分身
        的产物均可读」同口径；`project_id` 只是聚合过滤键，不是权限边界。并集读语义由
        `artifact_query_service.list` 提供：项目内参与记录 ∪ `hasn_artifacts.project_id` 直接
        命中 ∪ 挂靠容器名下产物。
        """
        result = await artifact_query_service.list(
            db,
            owner_hasn_id=owner_hasn_id,
            # 关键：不传 agent_hasn_id —— 项目内跨分身。
            agent_hasn_id=None,
            project_id=project_id,
            work_session_id=session_id,
            artifact_kind=kind,
            keyword=keyword,
            source_app_id=source_app_id,
            resource_kind=resource_kind,
            page=page,
            size=size,
        )
        return [cls._legacy_item(item) for item in result.items], result.total

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
        result = await artifact_query_service.list(
            db,
            owner_hasn_id=owner_hasn_id,
            artifact_kind=kind,
            page=page,
            size=size,
        )
        return [cls._legacy_item(item) for item in result.items], result.total

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
        result = await artifact_query_service.list(
            db,
            owner_hasn_id=owner_hasn_id,
            origin_ref=origin_ref,
            page=page,
            size=size,
        )
        return [cls._legacy_item(item) for item in result.items], result.total

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
        result = await artifact_query_service.list(
            db,
            owner_hasn_id=owner_hasn_id,
            work_session_id=session_id,
            page=page,
            size=size,
        )
        return [cls._legacy_item(item) for item in result.items], result.total

    @classmethod
    async def get_detail(cls, db: AsyncSession, *, owner_hasn_id: str, artifact_id: str) -> ArtifactDetail:
        """单条产物详情（含 display_url + download_url）。仅本 owner 可读。"""
        result = await artifact_query_service.list(
            db,
            owner_hasn_id=owner_hasn_id,
            artifact_id=artifact_id,
            size=1,
        )
        if not result.items:
            raise errors.NotFoundError(msg='产物不存在或无权访问')
        item = cls._legacy_item(result.items[0])
        row = (
            await db.execute(
                select(HasnArtifacts)
                .where(HasnArtifacts.artifact_id == artifact_id, HasnArtifacts.owner_hasn_id == owner_hasn_id)
                .limit(1)
            )
        ).scalar_one()
        return ArtifactDetail.model_validate(
            {
                **item.model_dump(),
                'body': row.body,
                'download_url': item.display_url,
                'metadata': row.meta_data or {},
            }
        )

    @classmethod
    async def update_content(
        cls, db: AsyncSession, *, owner_hasn_id: str, artifact_id: str, body: str, title: str | None = None
    ) -> None:
        """Owner 更新文本产物正文（markdown 编辑保存）。仅本 owner 的 active document 行可改。

        当前态的本体定位严格四选一，不能在仍保留 asset/resource/local locator 时额外写入 body。
        因此二进制、应用资源和本地产物会得到明确的请求错误，而不是把数据库约束异常泄漏为 500。
        """
        artifact_kind = (
            await db.execute(
                select(HasnArtifacts.artifact_kind)
                .where(
                    HasnArtifacts.artifact_id == artifact_id,
                    HasnArtifacts.owner_hasn_id == owner_hasn_id,
                    HasnArtifacts.status == 'active',
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if artifact_kind is None:
            raise errors.NotFoundError(msg='产物不存在或无权修改')
        if artifact_kind != 'document':
            raise errors.RequestError(msg='仅文本产物支持在线编辑，其他产物请使用其原始编辑入口')

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
        if getattr(result, 'rowcount', 0) == 0:
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
        if getattr(result, 'rowcount', 0) == 0:
            raise errors.NotFoundError(msg='产物不存在或无权删除')

    @classmethod
    async def soft_delete_by_resource_uri(cls, db: AsyncSession, *, owner_hasn_id: str, resource_uri: str) -> int:
        """按 (owner, resource_uri) strict 软删该资源的**全部** active 指针，返回软删条数。

        finance 等本地优先应用删除资源时用（05 §5.3a delete 分支）：业务行标 deleted 的同一
        云端事务里，把 hasn_artifacts 里指向这条 hasn://... 的所有 active 产物指针一并软删——
        否则留下「业务已删、产物栏仍是活链接」的分叉。与 soft_delete(artifact_id) 的区别是这里
        按资源 URI 批量（登记 UPSERT 去重后通常一行，但按 URI 删才是删除语义的正确表达）。

        strict 语义：调用方在 :sync 同事务内调它，DB 异常必须外抛触发整体回滚（不吞错）。
        软删 0 行是**允许**的——资源从未被登记（如主人手建未参与分身、或登记曾失败）不是错误，
        删除照常推进；不像 soft_delete(artifact_id) 那样把 0 行当 404。
        """
        result = await db.execute(
            update(HasnArtifacts)
            .where(
                HasnArtifacts.owner_hasn_id == owner_hasn_id,
                HasnArtifacts.resource_uri == resource_uri,
                HasnArtifacts.status == 'active',
            )
            .values(status='deleted')
        )
        return getattr(result, 'rowcount', 0) or 0


hasn_artifacts_service: HasnArtifactsService = HasnArtifactsService()
