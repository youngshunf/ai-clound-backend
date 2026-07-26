"""平台工具 · designsystem 域（设计系统生成应用，DS-P4 的云端权威 4 工具）。

把分身的 `hasn.designsystem.{import,save,list,get}` 从 hasn-node 本地 hasn-mcp 迁到**云端
platform MCP 工具**（不操作本地文件/数据 → 走云端，与 contact/message/plan/notification/artifact
同范式）：分身经 `/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `design_system_service`
/ `import_design_source`（in-process，**不再**经 daemon → `/api/v1/designsystem/agent/*` HTTP relay，
list/get 亦不再经 daemon `DesignSystemGateway` 本地镜像）。

另 4 个**确定性纯函数**工具（`compile_tokens`/`derive`/`validate`/`extract_components`）此前只在
hasn-node 本地 hasn-mcp（Rust `hasn_designsystem_core`，`execution_location=Local`），**云端分身
（Hermes cloud runtime）经 `/api/v1/mcp/streamable` 够不着**（本地工具对云端 runtime 不可见）。
TOOLMIG（2026-07-04·福仔「直接用云端 python 重写一遍，rust 侧逐渐退役」）：这 4 个纯函数已 Python
移植到 `backend.app.hasn_designsystem.core`，在此作**云端 platform MCP 工具**补齐云端分身能力——工具名 +
input_schema 与本地 hasn-mcp **逐字段 1:1**（分身引用不变）。它们是确定性纯计算（无 DB/无网络/无副作用），
故 `execution_location=cloud`、`source=platform`、**读类无 scope**；`generated_at` 由工具体注入
（仅产物溯源，不参与契约判定）。本地 Rust 4 工具暂留不动（设备分身保留离线能力），后续对拍等价后逐步退役，
收敛到 Python 单一实现源。

身份恒由 `agent_context`（取自 Agent JWT/MCP Key）注入：
- save → `Subject.agent(agent_hasn_id, owner_hasn_id)`（owner 隔离 + 协作权限由 service 强制）；
- list/get → `viewer_owner_hasn_id=owner_hasn_id`（可见域 builtin∪owner∪企业∪共享）。

- 工具名 + input_schema 与原 hasn-mcp `designsystem` 云端工具 **逐字段 1:1**（分身引用不变）。
- scope 与本地 1:1：写类 import/save 声明 `designsystem:write`（出厂 Allow）；读类 list/get 无 scope。
  三态闸门由 `server.call_tool` 统一判定，工具体不二次校验。
- save 成功落版后（写事务已提交）做两个 best-effort post-commit 副作用（独立事务、失败只 warn，
  绝不影响 save 结果），完整对齐 deck 的 register-on-write + finalize 完成卡（DSCARD·福仔「像 deck
  那样」）：① register-on-write——每次 save 都把该设计系统登记进 `hasn_artifacts`
  （`hasn://designsystem/{云端权威 id}`，带 work_session_id）→ 出现在「工作会话资源栏 / 分身产物
  tab」可点开查看；② 完成卡——内容写全时经 `route_message` 从分身发一张「打开设计系统」卡进主人主会话。
"""

from __future__ import annotations

import logging

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

# G6 使用点注册兜底（doc33 S3-1）：import 即注册 designsystem 资源适配器，保证 MCP 直连面判权时
# adapter 已在注册表（与 ai_native_app_registry 启动注册互为兜底，模块缓存保证进程内只注册一次）。
from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
from backend.app.hasn_designsystem.core import SourceToken as CoreSourceToken
from backend.app.hasn_designsystem.core import (
    compile_tokens as core_compile_tokens,
)
from backend.app.hasn_designsystem.core import css as core_css
from backend.app.hasn_designsystem.core import (
    derive as core_derive,
)
from backend.app.hasn_designsystem.core import (
    extract_components as core_extract_components,
)
from backend.app.hasn_designsystem.core import (
    summarize_gallery as core_summarize_gallery,
)
from backend.app.hasn_designsystem.core import (
    validate as core_validate,
)
from backend.app.hasn_designsystem.service import resource_adapter as _designsystem_resource_adapter  # noqa: F401
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.app.hasn_designsystem.service.import_service import import_design_source
from backend.app.hasn_designsystem.service.scene_guidance import build_scene_report
from backend.app.mcp.artifact_registration import merge_resource_uri, register_app_resource_artifact
from backend.app.mcp.context import get_current_project_id
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

log = logging.getLogger(__name__)

_SCOPE_WRITE = 'designsystem:write'

# ── G6 资源权限门声明（doc33 S3-1）──────────────────────────────────────────────────
# designsystem 工具经 MCP 直连面（server.py::call_tool 读 tool.resource_access）判权。designsystem
# manifest tools=[]（工具走平台 BaseTool，非 manifest tools[]），故这些声明**不进** manifest 注册期校验，
# 由下方 get/save 工具的 `resource_access` 属性直接暴露给门（与 deck 同范式）。service 层原有
# `_assert_can_read` / save 权限判定保留（防御纵深），门在 ask 审批前先按同一 `resolve_effective_permission`
# 内核多判一次，确定性无权先拒、不打扰主人审批。
# - get：design_system_id 必填 → 声明 viewer。
# - save：design_system_id 可空（null=新建，无实例可判）→ 声明 editor + required=False，缺省即跳过判权。
_RA_DS_VIEWER = [{'param': 'design_system_id', 'type': 'designsystem', 'need': 'viewer'}]
_RA_DS_VIEWER_OPT = [{'param': 'design_system_id', 'type': 'designsystem', 'need': 'viewer', 'required': False}]
_RA_DS_EDITOR = [{'param': 'design_system_id', 'type': 'designsystem', 'need': 'editor', 'required': False}]


async def _bump_designsystem_sync(db: Any, owner_hasn_id: str) -> None:
    """设计系统写点（save）后 → WSPUSH ``hasn.sync.invalidate(designsystem)`` 给该 owner 在线节点。

    在线 daemon 秒级对账本地镜像（read_through 回填），离线节点靠重连握手对账。best-effort，
    推送失败绝不影响写入（与 agent API `_bump_designsystem_sync` 同逻辑）。
    """
    try:
        from backend.app.hasn.service.sync_invalidate_service import KIND_DESIGNSYSTEM
        from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

        await sync_bump(KIND_DESIGNSYSTEM, db, owner_id=owner_hasn_id)
    except Exception as e:  # 推送 best-effort
        log.warning('[designsystem] sync invalidate 推送失败 (非致命): %s', e)


def _str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _owner_hasn_id(agent_context: AgentContext) -> str:
    """收紧“所有分身必须有主人”不变量，避免可空认证字段流入设计系统 owner 边界。"""
    owner = agent_context.owner_hasn_id
    if not owner:
        raise RuntimeError('designsystem tool: Agent 主人身份缺失')
    return owner


def _req_str(arguments: dict[str, Any], key: str) -> str:
    value = _str(arguments, key)
    if not value:
        raise RuntimeError(f"designsystem: '{key}' 必填且非空")
    return value


def _opt_int(arguments: dict[str, Any], key: str) -> int | None:
    value = arguments.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _resolve_save_platform_project_id(arguments: dict[str, Any], *, is_create: bool) -> str | None:
    """解析 save 的项目挂靠：显式入参优先；仅新建且缺字段时继承当前项目上下文。

    显式 ``null`` 表示调用方明确不挂项目，不能再回落 ContextVar；更新存量缺字段时不自动改挂。
    """
    if 'platform_project_id' in arguments:
        return _str(arguments, 'platform_project_id')
    return get_current_project_id() if is_create else None


def _now_iso() -> str:
    """确定性纯工具的 ``generated_at`` provenance（ISO8601/RFC3339）。

    纯函数核心的时间由调用方提供以保确定性；此值仅作产物溯源，**不参与契约判定逻辑**
    （对齐本地 hasn-mcp `now_iso`：`chrono::Utc::now().to_rfc3339()`）。
    """
    return datetime.now(timezone.utc).isoformat()


def _str_list(arguments: dict[str, Any], key: str) -> list[str]:
    """取一个字符串数组入参（非字符串项跳过、去空白、丢空），缺省 []。"""
    raw = arguments.get(key)
    if not isinstance(raw, list):
        return []
    return [v.strip() for v in raw if isinstance(v, str) and v.strip()]


def _parse_source_tokens(arguments: dict[str, Any]) -> list[CoreSourceToken]:
    """把入参 ``tokens_css`` / ``source_tokens`` 整形成 :class:`CoreSourceToken` 列表。

    对齐本地 hasn-mcp `parse_source_tokens`：优先用**非空**显式 ``source_tokens``（含血缘），
    否则从 ``tokens_css`` 的 ``:root`` 声明扫出。两者都空则报错（与本地同措辞）。
    """
    items = arguments.get('source_tokens')
    if isinstance(items, list):
        tokens: list[CoreSourceToken] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get('name')
            value = item.get('value')
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            name = name.strip()
            value = value.strip()
            if not name or not value:
                continue
            source = item.get('source')
            source = source.strip() if isinstance(source, str) and source.strip() else 'source_tokens'
            line = item.get('line')
            line = line if isinstance(line, int) and not isinstance(line, bool) and line >= 0 else None
            tokens.append(CoreSourceToken(name=name, value=value, source=source, line=line))
        if tokens:
            return tokens

    tokens_css = _str(arguments, 'tokens_css')
    if not tokens_css:
        raise RuntimeError(
            "designsystem.compile_tokens: 需提供 'tokens_css'（原始 :root 变量）或非空 'source_tokens' 数组"
        )
    declarations = core_css.parse_token_declarations(tokens_css)
    parsed = [CoreSourceToken(name=name, value=value, source='tokens.css', line=None) for name, value in declarations]
    if not parsed:
        raise RuntimeError("designsystem.compile_tokens: 'tokens_css' 未解析出任何 :root 自定义属性声明")
    return parsed


# ── DSGET·get 瘦身投影（分身取数省 token）──────────────────────────────────────────
# 默认 get 只保留「非画廊」内容字段：tokens.css（真源）+ design.md（设计说明）+ 契约报告（评分/问题）。
# 整包画廊大头 components_html / manifest / tailwind / design_tokens_json 一律砍出默认，改按需
# hasn.designsystem.get_gallery（可按场景取）——场景一多整包回灌会烧 token、污染上下文。
_GET_SUMMARY_KEEP_CONTENT = ('tokens_css', 'design_md', 'token_contract_report_json')
# current_revision 保留的非内容元字段（版本号/作者/bundle 指针等，体积小）。
_REVISION_META_FIELDS = (
    'id',
    'design_system_id',
    'rev_no',
    'author_kind',
    'author_id',
    'bundle_asset_id',
    'note',
    'created_time',
)


def _project_get_to_summary(full: dict[str, Any]) -> dict[str, Any]:
    """把 ``design_system_service.get`` 的整包结果投影成分身默认 ``get`` 的**瘦身视图**（DSGET）。

    顶层设计系统 meta 全留（含 score/grade/required_scenes/preview_swatches）；``current_revision`` 只留
    tokens.css + design.md + 契约报告 + 轻量 ``gallery_summary``（有哪些场景/各几件，**不含 HTML**），
    砍掉整包 ``components_html`` / ``components_manifest_json`` / ``tailwind_css`` / ``design_tokens_json``。
    分身要画廊 markup → 调 ``hasn.designsystem.get_gallery``（可按场景切片）。
    """
    out = {k: v for k, v in full.items() if k != 'current_revision'}
    rev = full.get('current_revision')
    if isinstance(rev, dict):
        slim: dict[str, Any] = {k: rev[k] for k in _REVISION_META_FIELDS if k in rev}
        for k in _GET_SUMMARY_KEEP_CONTENT:
            slim[k] = rev.get(k)
        slim['gallery_summary'] = core_summarize_gallery(rev.get('components_html'))
        out['current_revision'] = slim
    else:
        out['current_revision'] = rev
    return out


class DesignSystemImportTool(BaseTool):
    """`hasn.designsystem.import`：shadcn/github/screenshot/url → tokens.css 草稿（cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.import'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '从 shadcn registry / GitHub 仓 / 截图URL 扫 CSS 自定义属性，产出初始 tokens.css 草稿'
            '（给分身打底，非最终；交 compile_tokens 标准化）。云端拉取含 SSRF 闸。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'source': {'type': 'string', 'description': '来源类型：shadcn | github | screenshot | url'},
                'ref': {'type': 'string', 'description': 'registry item URL / owner/repo[#branch] / 页面 URL'},
            },
            'required': ['source', 'ref'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return [_SCOPE_WRITE]

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        source = _req_str(arguments, 'source')
        ref = _req_str(arguments, 'ref')
        # 纯拉取（无 db 写）：云端含 SSRF 闸；身份不参与（草稿不落库）。
        return await import_design_source(source, ref)


class DesignSystemSaveTool(BaseTool):
    """`hasn.designsystem.save`：组装完整 bundle → 写 hasn_designsystem 表 + 落一版 revision（cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.save'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '组装完整 bundle → 写云端 hasn_designsystem 表 + 落一版 revision + 注册供下游 picker。'
            '返回 {id, revision, content_hash, name, score, grade}。非幂等（每次落新版本）。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'design_system_id': {'type': 'integer', 'description': '可选：存量设计系统 id（缺省=新建）'},
                'slug': {'type': 'string', 'description': 'owner 内唯一短名'},
                'name': {'type': 'string', 'description': '展示名'},
                'content': {
                    'type': 'object',
                    'description': (
                        '四层 token 契约产物对象：{tokens_css, design_tokens_json, tailwind_css, '
                        'design_md, components_html, components_manifest_json, token_contract_report_json}'
                    ),
                },
                'category': {'type': 'string', 'description': '可选：品类'},
                'source_kind': {
                    'type': 'string',
                    'description': '可选：generated / imported_shadcn / imported_github / ...（缺省 generated）',
                },
                'score': {'type': 'integer', 'description': '可选：0–100 评分'},
                'grade': {'type': 'string', 'description': '可选：等级'},
                'required_scenes': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': (
                        '可选：组件画廊要求覆盖的交付物场景 id 列表'
                        '（brand_website/deck/poster/mobile；缺省=不改，owner 派发时设定）'
                    ),
                },
                'platform_project_id': {
                    'type': ['string', 'null'],
                    'format': 'uuid',
                    'description': '可选：新建设计系统挂靠的平台项目 id；缺字段时自动继承当前工作项目',
                },
            },
            'required': ['slug', 'name', 'content'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return [_SCOPE_WRITE]

    @property
    def resource_access(self) -> list[dict[str, Any]] | None:
        # 更新存量（design_system_id 非空）→ editor 判权；新建（缺省）→ required=False 跳过。
        return _RA_DS_EDITOR

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        slug = _req_str(arguments, 'slug')
        name = _req_str(arguments, 'name')
        content = arguments.get('content')
        if not content or not isinstance(content, dict):
            raise RuntimeError("designsystem.save: 'content' 必填（四层契约产物对象）")

        recommend = arguments.get('recommend_rebuild')
        # required_scenes：仅当入参显式带 key 才透传（缺省=不改，避免每次 save 抹掉 owner 已设的场景要求）。
        required_scenes = _str_list(arguments, 'required_scenes') if 'required_scenes' in arguments else None
        design_system_id = _opt_int(arguments, 'design_system_id')
        platform_project_id = _resolve_save_platform_project_id(
            arguments,
            is_create=design_system_id is None,
        )
        owner_hasn_id = _owner_hasn_id(agent_context)
        subject = Subject.agent(agent_context.agent_hasn_id, owner_hasn_id)

        # design_system_service.save 内部 self-commit（区别于 plan/artifact 的「只 flush」约定），
        # 故用**普通 session**（非 begin() 上下文管理器）——后者会被 service 的内部 commit 关闭其事务、
        # 令随后的 bump 撞 “closed transaction” 守卫。save 自提交后 bump 在新自起事务里写、末尾显式提交。
        async with async_db_session() as db:
            data = await design_system_service.save(
                db,
                subject=subject,
                design_system_id=design_system_id,
                slug=slug,
                name=name,
                content=content,
                category=_str(arguments, 'category'),
                source_kind=_str(arguments, 'source_kind') or 'generated',
                score=_opt_int(arguments, 'score'),
                grade=_str(arguments, 'grade'),
                recommend_rebuild=recommend if isinstance(recommend, bool) else False,
                bundle_asset_id=_str(arguments, 'bundle_asset_id'),
                note=_str(arguments, 'note'),
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
                required_scenes=required_scenes,
                platform_project_id=platform_project_id,
            )
            await _bump_designsystem_sync(db, owner_hasn_id)
            await db.commit()

        # save 已落库后（写事务已提交）的两个 post-commit 副作用（best-effort、独立事务、失败只 warn，
        # 绝不影响已落库的 save 结果）——完整对齐 deck 的 register-on-write + finalize 完成卡：
        # ① register-on-write：把该设计系统登记进 hasn_artifacts（hasn://designsystem/{id}），带上
        #    work_session_id → 出现在「工作会话资源栏 / 分身产物 tab」，可点开设计系统详情查看。
        # ② 完成卡：内容写全时，经 route_message 从分身发一张「打开设计系统」卡进主人主会话。
        registration = await self._register_designsystem_artifact_best_effort(agent_context, data)
        await self._deliver_completion_card_best_effort(agent_context, data)
        data.pop('completion_card', None)  # 内部完成信号，不外露给调用分身
        # doc36 §3.2：返回体带 `uri`——分身存完规范当场知道怎么打开它，不必二次查询。
        return merge_resource_uri(data, registration)

    @staticmethod
    async def _register_designsystem_artifact_best_effort(
        agent_context: AgentContext, data: dict[str, Any]
    ) -> ArtifactRegistration | None:
        """register-on-write（对齐 deck `_register_deck_artifact`）：**每次 save** 都把该设计系统登记进
        `hasn_artifacts`（resource_uri=`hasn://designsystem/{云端权威 id}`），带 work_session_id →
        出现在「工作会话资源栏 / 分身产物 tab」，且可点开设计系统详情查看（治「分身建/改了设计系统，
        工作会话产物列表却看不到」）。幂等 upsert（键 `(agent, designsystem:{id}, hasn://designsystem/{id})`）
        ——反复 save 只一条 active 行、会话归属只进不退。独立事务、失败只 warn，绝不影响 save 结果。

        doc36 U1：改走**公共接缝** `register_app_resource_artifact`（此前绕过接缝直调 service，
        故 `ROLLOUT` 守卫覆盖不到 designsystem）。返回 `ArtifactRegistration` 供 save 把 `uri` 放进返回体。
        """
        ds_id = data.get('id')
        if not isinstance(ds_id, int):
            return None
        title = data.get('name') if isinstance(data.get('name'), str) else None
        try:
            async with async_db_session.begin() as db:
                return await register_app_resource_artifact(
                    db,
                    app_id='designsystem',
                    resource_kind='designsystem.spec',
                    server_id=str(ds_id),
                    session_id=agent_context.session_id,
                    agent_hasn_id=agent_context.agent_hasn_id,
                    owner_hasn_id=_owner_hasn_id(agent_context),
                    title=(title or '').strip() or '设计系统',
                    source_tool='hasn.designsystem.save',
                )
        except Exception as e:  # 独立事务本身开/提交失败（接缝内部已吞登记错），仍 best-effort
            log.warning('[designsystem] register-on-write 事务失败（非致命）: %s', e)
            return None

    @staticmethod
    async def _deliver_completion_card_best_effort(agent_context: AgentContext, data: dict[str, Any]) -> None:
        """完成卡（对齐 deck `_run_deck_post_commit`）：save 判定「必填字段齐了」时透出的完成信号
        （`data['completion_card']`）→ 写事务**提交后**在独立会话里经 route_message 从分身发卡进主人主会话。

        route_message 自管 `db.commit()`，故必须独立会话（绝不能塞进 save 的事务，否则提前结束事务）。
        投递成功后回填 completed_notified_at（与 save 的门配套；首投失败则留空、下次完整 save 自愈补发，
        叠加 route_message 的 local_id 幂等 → 双保只发一张、不丢卡）。best-effort：失败只 warn。
        """
        card = data.get('completion_card')
        if not isinstance(card, dict):
            return
        ds_id = card.get('design_system_id')
        if not isinstance(ds_id, str) or not ds_id:
            return
        try:
            from backend.app.hasn.service.hasn_sessions_service import emit_designsystem_completion_card

            async with async_db_session() as db:
                await emit_designsystem_completion_card(
                    db,
                    owner_id=_owner_hasn_id(agent_context),
                    agent_id=agent_context.agent_hasn_id,
                    design_system_id=ds_id,
                    title=str(card.get('title') or ''),
                    summary=str(card.get('summary') or ''),
                )
            # 投递成功 → 回填 completed_notified_at（独立事务，幂等）。
            async with async_db_session() as db:
                await design_system_service.mark_completion_notified(db, int(ds_id))
        except Exception as e:  # 完成卡 best-effort，绝不影响 save 结果
            log.warning('[designsystem] 完成卡投递失败（非致命）: %s', e)


class DesignSystemListTool(BaseTool):
    """`hasn.designsystem.list`：列出分身可见的设计系统（builtin∪owner∪企业∪共享，cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.list'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '列出分身可见的设计系统（builtin∪owner∪企业∪共享）。**只返回轻量 meta**'
            '（id/name/slug/品类/评分/等级/预览色板/required_scenes，无正文/无画廊），省 token。'
            '返回 {items, total}。要某套详情调 get，要组件画廊调 get_gallery。确定性读。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'category': {'type': 'string', 'description': '可选：按品类过滤'},
                'enterprise_id': {'type': 'integer', 'description': '可选：企业域'},
                'limit': {'type': 'integer', 'description': '可选：页大小（默认 50，上限 200）'},
                'offset': {'type': 'integer', 'description': '可选：偏移（默认 0）'},
                'platform_project_id': {
                    'type': 'string',
                    'format': 'uuid',
                    'description': '可选：显式按平台项目过滤；缺省不按当前项目收窄',
                },
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        # 读类无 scope（确定性读，不设假闸门）。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = _opt_int(arguments, 'limit') or 50
        limit = max(1, min(limit, 200))
        offset = _opt_int(arguments, 'offset') or 0
        async with async_db_session() as db:
            return await design_system_service.list_visible(
                db,
                viewer_owner_hasn_id=_owner_hasn_id(agent_context),
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
                category=_str(arguments, 'category'),
                platform_project_id=_str(arguments, 'platform_project_id'),
                limit=limit,
                offset=max(0, offset),
            )


class DesignSystemGetTool(BaseTool):
    """`hasn.designsystem.get`：取一套设计系统详情（含当前版本完整内容，cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.get'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '取一套设计系统详情（**瘦身版·省 token**）：当前版本 tokens.css（真源色板）+ design.md（设计说明）'
            '+ 契约报告（评分/问题）+ gallery_summary（有哪些场景、各几件组件，**不含画廊 HTML**）。'
            '⚠️ 默认**不返回组件画廊 HTML**（场景一多整包会撑爆上下文）——要参考/编辑组件画廊请调 '
            'hasn.designsystem.get_gallery（可按场景取）。tailwind/design-tokens.json 可由 tokens.css '
            'derive 现推。确定性读。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'design_system_id': {'type': 'integer', 'description': '设计系统 id'},
                'enterprise_id': {'type': 'integer', 'description': '可选：企业域'},
            },
            'required': ['design_system_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    @property
    def resource_access(self) -> list[dict[str, Any]] | None:
        # get 的 design_system_id 必填 → viewer 判权。
        return _RA_DS_VIEWER

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        design_system_id = _opt_int(arguments, 'design_system_id')
        if design_system_id is None or design_system_id < 1:
            raise RuntimeError("designsystem.get: 'design_system_id' 必填且 ≥ 1")
        async with async_db_session() as db:
            full = await design_system_service.get(
                db,
                design_system_id=design_system_id,
                viewer_owner_hasn_id=_owner_hasn_id(agent_context),
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
            )
        # 瘦身投影：砍整包画廊，只回 tokens.css + design.md + 契约报告 + 场景摘要（DSGET·省 token）。
        return _project_get_to_summary(full)


class DesignSystemGetGalleryTool(BaseTool):
    """`hasn.designsystem.get_gallery`：按需取组件画廊 HTML（可按场景切片，cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.get_gallery'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '**按需**取一套设计系统的组件画廊 HTML（get 默认已不带画廊以省 token，要参考/编辑组件才调这个）。'
            '给 scene 只取该场景那一段画廊（带全量 <style> 保证自包含可渲染）——场景多时**优先按场景取**，别整包拉。'
            '要**编辑整套画廊**再存（save 是整包替换）时才不带 scene 取整包 components_html。'
            '可取场景 = brand_website/deck/poster/mobile；返回 {components_html, available_scenes, scene, slice_applied, ...}。确定性读。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'design_system_id': {'type': 'integer', 'description': '设计系统 id'},
                'scene': {
                    'type': 'string',
                    'description': '可选：只取该场景的画廊（brand_website/deck/poster/mobile）；'
                    '缺省=整包（编辑整套时用）。该场景无 <section> 容器/未知场景 → 诚实回退整包',
                },
                'enterprise_id': {'type': 'integer', 'description': '可选：企业域'},
            },
            'required': ['design_system_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    @property
    def resource_access(self) -> list[dict[str, Any]] | None:
        # get_gallery 的 design_system_id 必填 → viewer 判权（与 get/check_scenes 同 ACL）。
        return _RA_DS_VIEWER

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        design_system_id = _opt_int(arguments, 'design_system_id')
        if design_system_id is None or design_system_id < 1:
            raise RuntimeError("designsystem.get_gallery: 'design_system_id' 必填且 ≥ 1")
        async with async_db_session() as db:
            return await design_system_service.get_gallery(
                db,
                design_system_id=design_system_id,
                viewer_owner_hasn_id=_owner_hasn_id(agent_context),
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
                scene=_str(arguments, 'scene'),
            )


class DesignSystemCheckScenesTool(BaseTool):
    """`hasn.designsystem.check_scenes`：自查组件画廊场景是否配齐 + 缺什么、怎么补（cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.check_scenes'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '自查一套设计系统的组件画廊「场景是否配齐」：交叉 owner 要求覆盖的 required_scenes 与当前 '
            'components.html 里 data-ds-scene/data-ds-component 标记**实际检测到**的标准组件，逐场景返回'
            '「已配齐 X/Y · 缺哪几件」+ 每件缺失组件「应包含什么、怎么用标记补」的可执行指引。'
            '⚠️ required_scenes 只是「要求覆盖哪些场景」的声明，不等于「已配齐」——本工具给的才是真实覆盖度，'
            '完成前务必调它确认 complete=true，别只看 required_scenes 就说「全套齐全」。确定性读。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'design_system_id': {
                    'type': 'integer',
                    'description': '要自查的设计系统 id（读它存的 components.html 与 required_scenes）；'
                    '缺省时改用下面内联的 components_html 做存前 dry-run',
                },
                'components_html': {
                    'type': 'string',
                    'description': '可选：直接对这段 components.html 检测（存前 dry-run 自己的草稿）；'
                    '与 design_system_id 同时给则用它覆盖库里的 HTML',
                },
                'required_scenes': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '可选：要求覆盖的场景 id（brand_website/deck/poster/mobile）；'
                    '缺省时用库里存的（或默认 [brand_website]）',
                },
                'enterprise_id': {'type': 'integer', 'description': '可选：企业域'},
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    @property
    def resource_access(self) -> list[dict[str, Any]] | None:
        # 给了 design_system_id → viewer 判权（读它的 HTML/场景要求）；纯内联 dry-run（缺省）→ required=False 跳过。
        return _RA_DS_VIEWER_OPT

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        design_system_id = _opt_int(arguments, 'design_system_id')
        components_html = _str(arguments, 'components_html')
        required_scenes = _str_list(arguments, 'required_scenes') if 'required_scenes' in arguments else None

        # 内联 dry-run（无 id）：不读库，直接对入参 HTML 检测；此时 components_html 必填。
        if design_system_id is None:
            if not components_html:
                raise RuntimeError(
                    "designsystem.check_scenes: 需提供 'design_system_id'（自查已存设计系统），"
                    "或提供 'components_html'（对草稿 dry-run）"
                )
            return build_scene_report(required_scenes, components_html)

        # by-id：读库 + 判权（与 get 同 ACL），现读现检测当前 components.html。
        if design_system_id < 1:
            raise RuntimeError("designsystem.check_scenes: 'design_system_id' 需 ≥ 1")
        async with async_db_session() as db:
            return await design_system_service.scene_coverage_report(
                db,
                design_system_id=design_system_id,
                viewer_owner_hasn_id=_owner_hasn_id(agent_context),
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
                components_html_override=components_html,
                required_scenes_override=required_scenes,
            )


# ── 确定性纯函数工具（TOOLMIG：Python 移植 hasn_designsystem_core，云端分身可用）──────────
# 这 4 个不碰 DB / 不发网络 / 无副作用——直调 `backend.app.hasn_designsystem.core` 纯函数。
# `execution_location='cloud'`、`source` 默认 'platform'、读类无 scope；`generated_at` 工具体注入。
# 工具名 + input_schema 与本地 hasn-mcp（Rust）**逐字段 1:1**（分身引用不变）。


class DesignSystemCompileTokensTool(BaseTool):
    """`hasn.designsystem.compile_tokens`：原始变量 / DESIGN.md 描述 → 四层 token 契约 tokens.css（纯函数）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.compile_tokens'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '把原始变量 / DESIGN.md 描述标准化为四层 token 契约 tokens.css（标准命名 + 分层 + '
            '缺槽别名回填）。返回 {tokens_css, report}。确定性纯函数。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'tokens_css': {
                    'type': 'string',
                    'description': '原始 :root 变量块（从 DESIGN.md 描述或导入草稿来），将被标准化',
                },
                'source_tokens': {
                    'type': 'array',
                    'description': '可选：显式源 token 数组 [{name, value, source?, line?}]（优先于 tokens_css）',
                },
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        # 确定性纯计算无特权动作 → 无 scope（避免假闸门）。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        source_tokens = _parse_source_tokens(arguments)
        contract = core_compile_tokens(source_tokens, _now_iso())
        return {'tokens_css': contract.tokens_css, 'report': contract.report}


class DesignSystemDeriveTool(BaseTool):
    """`hasn.designsystem.derive`：tokens.css → design-tokens.json + tailwind-v4.css（纯函数）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.derive'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '从 tokens.css 派生 design-tokens.json（含分层/血缘骨架）与 tailwind-v4.css（@theme 映射，'
            '不另定义值）。返回 {design_tokens_json, tailwind_v4_css}。确定性纯函数。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'tokens_css': {'type': 'string', 'description': '标准化后的 tokens.css（compile_tokens 的产物）'},
            },
            'required': ['tokens_css'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        tokens_css = _req_str(arguments, 'tokens_css')
        return core_derive(tokens_css, _now_iso())


class DesignSystemValidateTool(BaseTool):
    """`hasn.designsystem.validate`：四层契约校验 + 评分（质量门，纯函数）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.validate'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '四层契约校验 + A2/B-slot 完整性 + 标准命名漂移检查 + 0–100 评分 / grade / '
            'recommendRebuild + 问题清单（质量门）。确定性纯函数。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'tokens_css': {'type': 'string', 'description': '待校验的 tokens.css'},
                'components_html': {
                    'type': 'string',
                    'description': '可选：components.html（用于 token 引用 + 反模式校验）',
                },
                'allowed_extensions': {
                    'type': 'array',
                    'description': '可选：per-brand C-extension 白名单（空表则严格只认 schema）',
                },
            },
            'required': ['tokens_css'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        tokens_css = _req_str(arguments, 'tokens_css')
        return core_validate(
            tokens_css,
            _now_iso(),
            components_html=_str(arguments, 'components_html'),
            allowed_extensions=_str_list(arguments, 'allowed_extensions'),
        )


class DesignSystemExtractComponentsTool(BaseTool):
    """`hasn.designsystem.extract_components`：components.html → components.manifest.json（纯函数）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.extract_components'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '从 components.html 抽取 components.manifest.json（selector/class/element 统计 + '
            '用到的 token 列表 + 反模式字面量盘点）。确定性纯函数。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'brand_id': {'type': 'string', 'description': '品牌 / 设计系统 id'},
                'components_html': {'type': 'string', 'description': 'components.html 内容（分身创作的组件 fixture）'},
                'tokens_css': {
                    'type': 'string',
                    'description': '可选：tokens.css（缺省时退回从 HTML 首个 :root 提取声明名）',
                },
            },
            'required': ['brand_id', 'components_html'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        brand_id = _req_str(arguments, 'brand_id')
        fixture_html = _req_str(arguments, 'components_html')
        return core_extract_components(brand_id, fixture_html, tokens_css=_str(arguments, 'tokens_css'))


DESIGNSYSTEM_TOOLS: list[BaseTool] = [
    # 云端权威（操作云端数据）
    DesignSystemImportTool(),
    DesignSystemSaveTool(),
    DesignSystemListTool(),
    DesignSystemGetTool(),
    # 组件画廊按需取（DSGET；get 默认瘦身不带画廊，分身参考/编辑组件时按需取，可按场景切片省 token）
    DesignSystemGetGalleryTool(),
    # 组件画廊场景自查（DSGAL；读设计系统 required_scenes × 当前 HTML 实检覆盖 → 缺什么/怎么补）
    DesignSystemCheckScenesTool(),
    # 确定性纯函数（TOOLMIG：Python 移植，云端分身可用；本地 Rust 同名工具暂留待退役）
    DesignSystemCompileTokensTool(),
    DesignSystemDeriveTool(),
    DesignSystemValidateTool(),
    DesignSystemExtractComponentsTool(),
]
