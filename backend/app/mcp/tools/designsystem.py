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
- save 成功落版后 best-effort 登记一条指向 bundle 资产的可下载产物（AF/D2，对齐原 daemon
  `capture_best_effort`）——独立事务、失败只 warn，绝不影响 save 结果；无 bundle 资产则不臆造。
"""

from __future__ import annotations

import logging

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
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
    validate as core_validate,
)
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.app.hasn_designsystem.service.import_service import import_design_source
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

log = logging.getLogger(__name__)

_SCOPE_WRITE = 'designsystem:write'


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


def _req_str(arguments: dict[str, Any], key: str) -> str:
    value = _str(arguments, key)
    if not value:
        raise RuntimeError(f"designsystem: '{key}' 必填且非空")
    return value


def _opt_int(arguments: dict[str, Any], key: str) -> int | None:
    value = arguments.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
            },
            'required': ['slug', 'name', 'content'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return [_SCOPE_WRITE]

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        slug = _req_str(arguments, 'slug')
        name = _req_str(arguments, 'name')
        content = arguments.get('content')
        if not content or not isinstance(content, dict):
            raise RuntimeError("designsystem.save: 'content' 必填（四层契约产物对象）")

        recommend = arguments.get('recommend_rebuild')
        subject = Subject.agent(agent_context.agent_hasn_id, agent_context.owner_hasn_id)

        # design_system_service.save 内部 self-commit（区别于 plan/artifact 的「只 flush」约定），
        # 故用**普通 session**（非 begin() 上下文管理器）——后者会被 service 的内部 commit 关闭其事务、
        # 令随后的 bump 撞 “closed transaction” 守卫。save 自提交后 bump 在新自起事务里写、末尾显式提交。
        async with async_db_session() as db:
            data = await design_system_service.save(
                db,
                subject=subject,
                design_system_id=_opt_int(arguments, 'design_system_id'),
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
            )
            await _bump_designsystem_sync(db, agent_context.owner_hasn_id)
            await db.commit()

        # save 已落库后：best-effort 登记 bundle 产物（独立事务，失败只 warn，不影响 save 结果）。
        await self._record_bundle_artifact_best_effort(agent_context, data)
        return data

    @staticmethod
    async def _record_bundle_artifact_best_effort(agent_context: AgentContext, data: dict[str, Any]) -> None:
        """save 携 revision.bundle_asset_id → 登记一条 asset 类（document）可下载产物 + 语义元数据。

        对齐原 hasn-mcp daemon `capture_best_effort`：无 bundle 资产则不登记（零 fake，不臆造资源指针）；
        设计系统**不是**已注册的 hasn:// 资源域，故以 asset 为本体（不产 resource 指针）。
        """
        revision = data.get('revision') if isinstance(data.get('revision'), dict) else {}
        bundle_asset_id = revision.get('bundle_asset_id')
        if not isinstance(bundle_asset_id, str) or not bundle_asset_id.strip():
            return

        metadata: dict[str, Any] = {}
        for key in ('slug', 'grade'):
            if isinstance(data.get(key), str):
                metadata[key] = data[key]
        if isinstance(data.get('id'), int):
            metadata['design_system_id'] = data['id']
        if isinstance(data.get('score'), int):
            metadata['score'] = data['score']
        if isinstance(revision.get('rev_no'), int):
            metadata['rev_no'] = revision['rev_no']

        params = RecordArtifactParam(
            kind='document',
            title=data.get('name') if isinstance(data.get('name'), str) else None,
            asset_id=bundle_asset_id.strip(),
            source_kind='tool_output',
            source_tool='hasn.designsystem.save',
            metadata=metadata,
        )
        try:
            async with async_db_session.begin() as db:
                await hasn_artifacts_service.record(
                    db,
                    agent_hasn_id=agent_context.agent_hasn_id,
                    owner_hasn_id=agent_context.owner_hasn_id,
                    params=params,
                )
        except Exception as e:  # 产物登记 best-effort
            log.warning('[designsystem] bundle 产物登记失败 (非致命): %s', e)


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
        return '列出分身可见的设计系统（builtin∪owner∪企业∪共享）。返回 {items, total}。确定性读。'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'category': {'type': 'string', 'description': '可选：按品类过滤'},
                'enterprise_id': {'type': 'integer', 'description': '可选：企业域'},
                'limit': {'type': 'integer', 'description': '可选：页大小（默认 50，上限 200）'},
                'offset': {'type': 'integer', 'description': '可选：偏移（默认 0）'},
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
                viewer_owner_hasn_id=agent_context.owner_hasn_id,
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
                category=_str(arguments, 'category'),
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
            '取一套设计系统详情（含当前版本 tokens.css / design-tokens.json / tailwind / design.md / '
            'components / 契约报告）。确定性读。'
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

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        design_system_id = _opt_int(arguments, 'design_system_id')
        if design_system_id is None or design_system_id < 1:
            raise RuntimeError("designsystem.get: 'design_system_id' 必填且 ≥ 1")
        async with async_db_session() as db:
            return await design_system_service.get(
                db,
                design_system_id=design_system_id,
                viewer_owner_hasn_id=agent_context.owner_hasn_id,
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
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
    # 确定性纯函数（TOOLMIG：Python 移植，云端分身可用；本地 Rust 同名工具暂留待退役）
    DesignSystemCompileTokensTool(),
    DesignSystemDeriveTool(),
    DesignSystemValidateTool(),
    DesignSystemExtractComponentsTool(),
]
