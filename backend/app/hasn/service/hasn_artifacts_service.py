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
from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.schema.resource_descriptor import ResourceDescriptor

# artifact_kind / source_kind 的**校验已上移到 schema 的 Literal**（doc35 §7）——越界 422，不再静默归一。
#
# 旧的 `_ALLOWED_KINDS` 白名单语义是「归一」而非「拒绝」：越界不报错、**静默**改写成 other。
# 与 daemon 产出闸的精确字符串相等组合起来就是死锁：模板声明 kind → 分身**真的产出了** →
# 登记时白名单不认、降级成 other → 闸门比对 `kind != other` → 判定「未产出」→ 节点空转 refill。
# 这里不再留白名单，就是为了让「越界」在**写入时**炸出来，而不是变成一条说谎的行（§1.5）。
#
# 产出动作（doc34）：新增 / 修改。越界归一为 create（action 不参与闸门判定，归一无死锁风险）。
_ALLOWED_ACTIONS = {'create', 'update'}


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

    @staticmethod
    def _assert_invariants(
        *,
        kind: str,
        resource_kind: str | None,
        source_kind: str,
        resource_uri: str | None,
        body: str | None,
        asset_id: str | None,
        local_path: str | None,
        source_app_id: str | None,
    ) -> None:
        """I1–I4/I6 一致性不变量（doc35 §6）：kind 与「本体存在哪」必须自洽。

        保留显式 `artifact_kind`（而非从字段推导）的代价，就是它可能与实际通道打架。
        不变量是那个代价的对价：矛盾数据在**写入时**就被拒，而不是等 UI 渲染时才发现
        「一个 hasn://studio/projects/{id} 挂着视频播放器，播什么？」（§1.2）。
        """
        if kind == 'resource':
            # I1：应用资源必须真的有 hasn:// 应用资源 URI（域 ≠ asset——asset 是二进制本体，不是应用资源）。
            if not resource_uri or not resource_uri.startswith('hasn://'):
                raise errors.RequestError(msg='artifact_kind=resource 必须带 hasn:// 应用资源 URI')
            if resource_uri.startswith('hasn://asset/'):
                raise errors.RequestError(msg='hasn://asset/ 是资产本体引用，不是应用资源；请用 image/video/voice/file')
            if not source_app_id:
                raise errors.RequestError(msg='artifact_kind=resource 必须带 source_app_id（哪个应用产的）')
            if not resource_kind:
                raise errors.RequestError(msg='artifact_kind=resource 必须带 resource_kind（是什么资源）')
        elif kind == 'document':
            # I2：document 的语义就是「body 直接存 markdown」，没有 body 就是空壳。
            if not body:
                raise errors.RequestError(msg='artifact_kind=document 必须带 body（markdown 正文入库）')
        elif kind in ('image', 'video', 'voice', 'file'):
            # I3：二进制族必须有本体——asset_id（云端桶）或 local_path（本地权威）。
            if not asset_id and not local_path:
                raise errors.RequestError(msg=f'artifact_kind={kind} 必须带 asset_id 或 local_path')

        # I4：source_kind='app' ⟺ artifact_kind='resource'（双向充要，两条都钉）。
        # 这条挡住的正是 doc35 §1.2 那个病灶标本：studio 产的**视频**想标 source_kind='app'
        # （「它确实是 studio 应用产的」），但它是 asset 型产物，不是应用资源——真标上去，
        # UI 就会拿 AppIcon 去渲染一个该播放的视频。应用资源那条（studio.project）另有登记。
        if source_kind == 'app' and kind != 'resource':
            raise errors.RequestError(msg='source_kind=app 仅用于应用资源产物（artifact_kind 须为 resource）')
        if kind == 'resource' and source_kind != 'app':
            raise errors.RequestError(msg='artifact_kind=resource 的来源必须是 app')
        # ⚠️ doc35 §6 的 I4 原文把 `source_app_id 非空` 也串进了这条充要链（即非应用资源不得带
        # source_app_id）。**此处只强制上面两条，第三条刻意不强制**：§2 的维度表把 source_app_id
        # 定义为「是哪个应用的」，而 imagelab / reel / film 用工具产出的图片视频，诚实答案就是
        # 那个应用（doc34 已在这么存、已有测试钉）。强制第三条＝逼这些行把真话抹成 NULL，
        # 且要跨仓摘掉 3 处 with_source_app_id，收益只有「少一列冗余」。留待福仔定夺。

        # I6：resource_kind 非空 → 必是应用资源（非应用产物没有「应用内资源类型」可言）。
        if resource_kind and kind != 'resource':
            raise errors.RequestError(msg='resource_kind 仅在 artifact_kind=resource 时有意义')

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
        # 产物本体四选一：body（文本/markdown 直接入库）/ asset_id（二进制）/
        # resource_uri（hasn:// 资源）/ local_path（本地文件，云端只存指针，doc34）。
        if not params.asset_id and not params.resource_uri and not params.body and not params.local_path:
            raise errors.RequestError(msg='产物必须带 body、asset_id、resource_uri 或 local_path 其一')

        # 本地路径必须带设备归属：没有 node_id 的绝对路径换台设备就是死路径，
        # UI 也无从判断「本机可打开」还是「在其他设备上」（doc34 §2.1）。
        if params.local_path and not params.node_id:
            raise errors.RequestError(msg='本地路径产物必须同时提供 node_id（产出设备）')

        # kind / source_kind 到这里已由 Literal 保证合法（越界在 schema 层就 422 了）。
        kind = params.kind
        source_kind = params.source_kind
        action = params.action if params.action in _ALLOWED_ACTIONS else 'create'
        conv = cls._coerce_uuid(params.conversation_id)

        # I1–I4/I6 不变量：kind 与「本体存在哪」必须自洽（doc35 §6）。
        # 保留显式 kind 的代价就是它可能与通道打架——不在写入时拦住，读时才发现就晚了（§11）。
        cls._assert_invariants(
            kind=kind,
            resource_kind=params.resource_kind,
            source_kind=source_kind,
            resource_uri=params.resource_uri,
            body=params.body,
            asset_id=params.asset_id,
            local_path=params.local_path,
            source_app_id=params.source_app_id,
        )

        # 本地文件产物幂等：同一文件在一次会话里反复写只留一行。这是 runtime 文件捕获
        # （write_file/patch 每次都上报）不把产物列表刷成流水账的关键——分身改 10 次
        # report.md 是 1 个产物，不是 10 个（doc34 §2.3）。
        if params.local_path and params.node_id and params.session_id:
            local_row = (
                await db.execute(
                    select(HasnArtifacts)
                    .where(
                        HasnArtifacts.agent_hasn_id == agent_hasn_id,
                        HasnArtifacts.session_id == params.session_id,
                        HasnArtifacts.node_id == params.node_id,
                        HasnArtifacts.local_path == params.local_path,
                        HasnArtifacts.status == 'active',
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if local_row:
                # action 只进不退：首次登记为 create 的行不被后续 update 覆盖——
                # 「这个文件是这个分身新建的」是稳定事实，不该被改稿抹掉（doc34 §2.2）。
                if local_row.action != 'create':
                    local_row.action = action
                if params.title:
                    local_row.title = params.title
                if params.summary:
                    local_row.summary = params.summary
                local_row.kind = kind
                if params.metadata:
                    local_row.meta_data = params.metadata
                await db.flush()
                return local_row.artifact_id

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
            resource_kind=(params.resource_kind or None),
            title=(params.title or None),
            summary=(params.summary or None),
            body=(params.body or None),
            asset_id=(params.asset_id or None),
            resource_uri=(params.resource_uri or None),
            local_path=(params.local_path or None),
            node_id=(params.node_id or None),
            origin_ref=(params.origin_ref or None),
            conversation_id=conv,
            message_id=params.message_id,
            session_id=(params.session_id or None),
            source_tool=(params.source_tool or None),
            source_app_id=(params.source_app_id or None),
            source_kind=source_kind,
            action=action,
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
        kind = cls._resolve_artifact_kind(descriptor)
        effective_dispatch_id = dispatch_id or f'{app_id}:{server_id}'
        origin_ref = cls._build_origin_ref(descriptor, app_id=app_id, server_id=server_id)

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
            # descriptor 是权威：resource_kind 随 descriptor 自愈（存量行在下次写点补上）。
            if existing_row.resource_kind != descriptor.resource_kind:
                existing_row.resource_kind = descriptor.resource_kind
                changed = True
            # origin_ref 同样随 descriptor 自愈（05 §1.2 第 4 条）：应用后来声明 ref_type 进入多资源模式后，
            # 存量行仍是旧的 resource:{app}:{id} 单资源形状 —— 那形状喂给 resolve_resource_descriptor
            # 会返 (None, None)，从此按业务对象反查不到、完成卡丢资源入口。下次写点即修正。
            if existing_row.origin_ref != origin_ref:
                existing_row.origin_ref = origin_ref
                changed = True
            if session_id and existing_row.session_id != session_id:
                existing_row.session_id = session_id
                changed = True
            # project_id 同样只进不退（doc38 §5.1 自动打标·只进不退）：分身在项目中反复写同一资源，
            # 首次带上 project_id 后即锁定；后续非项目直调（project_id=None）不得把它抹成 None。
            if project_id and str(existing_row.project_id or '') != project_id:
                existing_row.project_id = project_id
                changed = True
            new_title = title or None
            if new_title and existing_row.title != new_title:
                existing_row.title = new_title
                changed = True
            new_summary = summary or None
            if new_summary and existing_row.summary != new_summary:
                existing_row.summary = new_summary
                changed = True
            # doc34 §3：来源应用回填——存量行（source_app_id 列上线前登记的）在下一次写点自愈，
            # 免得老产物永远显示不出应用图标。descriptor 派生的 app_id 是权威，直接覆盖。
            if existing_row.source_app_id != app_id:
                existing_row.source_app_id = app_id
                changed = True
            if changed:
                await db.flush()
            return ArtifactRegistration(artifact_id=existing_row.artifact_id, resource_uri=resource_uri)

        artifact_id = cls.gen_artifact_id()
        row = HasnArtifacts(
            artifact_id=artifact_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            kind=kind,
            # doc35 §4：这份数据 descriptor 里早就有且精确（能区分 knowledge.base 知识库 与
            # knowledge.document 知识文档），以前登记时被丢掉。存原值，UI 查 registry 取展示名。
            resource_kind=descriptor.resource_kind,
            title=(title or None),
            summary=(summary or None),
            resource_uri=resource_uri,
            # origin_ref 存**云端权威** id（不存本地 id），按业务对象可反查产物；
            # 多资源应用带 ref_type 段，形状由 _build_origin_ref 唯一决定。
            origin_ref=origin_ref,
            session_id=(session_id or None),
            # doc38 §5.1：产物挂靠平台项目（可空；仅聚合过滤键，非权限边界）。
            project_id=(project_id or None),
            source_tool=(source_tool or None),
            # doc34 §3：应用资源产物的来源应用由 descriptor 派生，UI 据此显示应用图标（权威列，不靠反推）。
            source_app_id=app_id,
            # doc35 §5：去硬编码 'tool_output'。旧值让 source_kind 对应用资源零信息量
            # （95/141 行全是同一个值）；'app' 才是实话，UI 据它直接取 AppIcon(source_app_id)。
            source_kind='app',
            dispatch_id=effective_dispatch_id,
            meta_data={},
            status='active',
        )
        db.add(row)
        await db.flush()
        return ArtifactRegistration(artifact_id=artifact_id, resource_uri=resource_uri)

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
            # doc35 §4.3：UI 要显示「知识库」还是「设计系统规范」→ 据它查 registry 拿 descriptor
            # 的展示名/图标（与应用图标同源）。零硬编码、新应用零改前端。
            resource_kind=row.resource_kind,
            title=row.title,
            summary=row.summary,
            body=row.body,
            asset_id=row.asset_id,
            resource_uri=row.resource_uri,
            # doc34 §4：本地产物只回指针 + 产出设备，UI 据此分叉「本机可直接打开」/「在其他设备上」。
            local_path=row.local_path,
            node_id=row.node_id,
            origin_ref=row.origin_ref,
            conversation_id=str(row.conversation_id) if row.conversation_id else None,
            message_id=row.message_id,
            session_id=row.session_id,
            source_tool=row.source_tool,
            # doc34 §3：来源应用是权威列，UI 据此显示应用图标（不再靠 resource_uri/source_tool 反推）。
            source_app_id=row.source_app_id,
            source_kind=row.source_kind,
            action=row.action,
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
        source_app_id: str | None = None,
        resource_kind: str | None = None,
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
        if source_app_id:
            conds.append(HasnArtifacts.source_app_id == source_app_id)
        if resource_kind:
            conds.append(HasnArtifacts.resource_kind == resource_kind)
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
