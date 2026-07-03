"""平台工具 · diag 域（错误诊断，doc21 §8 · 模块 21）。

平台运维分身（「平台运维分析师」）经 `/api/v1/mcp/streamable` 直达云端，工具体直调云端
权威 `error_issue_service`（in-process）读/处置**跨 owner** 的平台错误 issue。与 task/plan/
designsystem 同范式（云端 platform 工具，不依赖本地操作）。

与 owner-scoped 工具（task/plan）的**关键区别**：diag 是**平台特权**能力——运维分身跨所有
owner 读全量错误聚合、改 issue 状态，**没有 owner 隔离**（错误遥测是设备/节点级、面向平台
维护者，不是某个 owner 的私有数据）。身份不入请求体；处置写审计留痕用 `agent_hasn_id`。

- 六工具（doc21 §8.2）：list_issues/get_issue/list_occurrences/stats（读，`diag:read:all`）、
  update_issue/resolve_issue（写，`diag:manage`）。
- 两 scope 均命中特权前缀 `diag:`（`platform_scopes.PRIVILEGED_SCOPES`）→ **G1 平台特权门**
  （doc18 §4.1）统一判定：只有经 Admin 授予表 ∪ ENV bootstrap 拿到 `diag:*` 授予的运维分身
  才可见/可调；普通分身发现面隐身、执行面 TOOL_NOT_FOUND 泛化（不确认存在性）。
- 三态（G5）**出厂 Allow**：platform 工具 `default_mode='allow'` 且无 manifest human_confirmation
  → 运维分身拿到特权后直接放行（owner 三态可覆盖收紧，但三态非放行依据——放行由 G1 授予决定）。
- 三态闸门 + G1 由 `server.call_tool` → `tool_exposure.evaluate` 统一判定，工具体不二次校验。
- 读走 `async_db_session()`；写走 `async_db_session.begin()` 自动提交（service 只 flush 不 commit）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from backend.app.hasn_diag.service import error_issue_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.common.exception import errors
from backend.database.db import async_db_session

NAMESPACE = 'hasn.diag'
SCOPE_READ = 'diag:read:all'
SCOPE_MANAGE = 'diag:manage'

Handler = Callable[[Any, AgentContext, dict[str, Any]], Awaitable[Any]]


# ── helpers ──────────────────────────────────────────────────────────────────
def _parse_dt(raw: object, field: str) -> datetime | None:
    """ISO8601 字符串 → datetime；None/空 → None；非法 → RequestError（边界处校验）。"""
    if not raw:
        return None
    if not isinstance(raw, str):
        raise errors.RequestError(msg=f'{field} 需为 ISO8601 时间字符串')
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise errors.RequestError(msg=f'{field} 非法时间格式：{raw}') from exc


# ── handlers（读：跨 owner 全量，无隔离；写：actor=运维分身 hasn_id 留痕）─────────────
async def _h_list_issues(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await error_issue_service.list_issues(
        db,
        status=args.get('status', 'open'),
        source=args.get('source'),
        severity=args.get('severity'),
        since=_parse_dt(args.get('since'), 'since'),
        stale_days=args.get('stale_days'),
        limit=args.get('limit'),
        cursor=args.get('cursor'),
    )


async def _h_get_issue(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await error_issue_service.get_issue(
        db, fingerprint=str(args['fingerprint']), occurrence_limit=int(args.get('occurrence_limit') or 10)
    )


async def _h_list_occurrences(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await error_issue_service.list_occurrences(
        db, fingerprint=str(args['fingerprint']), limit=args.get('limit'), cursor=args.get('cursor')
    )


async def _h_stats(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await error_issue_service.stats(db)


async def _h_update_issue(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await error_issue_service.update_issue(
        db,
        fingerprint=str(args['fingerprint']),
        actor_hasn_id=ctx.agent_hasn_id,
        issue_url=args.get('issue_url'),
        pr_url=args.get('pr_url'),
        note=args.get('note'),
    )


async def _h_resolve_issue(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await error_issue_service.resolve_issue(
        db,
        fingerprint=str(args['fingerprint']),
        actor_hasn_id=ctx.agent_hasn_id,
        status=str(args['status']),
        resolution_type=str(args['resolution_type']),
        resolution_note=str(args['resolution_note']),
        fixed_in_version=args.get('fixed_in_version'),
        duplicate_of_fingerprint=args.get('duplicate_of_fingerprint'),
        snooze_until=_parse_dt(args.get('snooze_until'), 'snooze_until'),
    )


# ── 工具规格（action → name=hasn.diag.<action>）────────────────────────────────
_FINGERPRINT = {'type': 'string', 'minLength': 1, 'description': '错误问题指纹（dedup_key）'}
_CURSOR = {'type': ['string', 'null'], 'description': 'keyset 分页游标（上一页 next_cursor）'}
_LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 100, 'description': '默认 20，上限 100'}

_SPECS: list[dict[str, Any]] = [
    {
        'action': 'list_issues',
        'write': False,
        'scopes': [SCOPE_READ],
        'handler': _h_list_issues,
        'desc': (
            '列平台错误 issue（按 fingerprint 聚合，last_seen_at 倒序 keyset 分页）。status 默认 open；'
            'stale_days 配 status=investigating 做孤儿回扫。跨 owner 全量（平台运维视角）。确定性读。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'status': {
                    'type': ['string', 'null'],
                    'description': "状态过滤 open/investigating/resolved/skipped/wontfix；缺省 open，传 null 不过滤",
                },
                'source': {'type': ['string', 'null'], 'description': '来源过滤：daemon/hermes/cloud_runtime'},
                'severity': {'type': ['string', 'null'], 'description': '严重度过滤：error/warn'},
                'since': {'type': ['string', 'null'], 'description': 'ISO8601：只看 last_seen_at 晚于此的'},
                'stale_days': {'type': ['integer', 'null'], 'description': '孤儿回扫：只留 last_seen_at 早于 N 天前的'},
                'limit': _LIMIT,
                'cursor': _CURSOR,
            },
        },
    },
    {
        'action': 'get_issue',
        'write': False,
        'scopes': [SCOPE_READ],
        'handler': _h_get_issue,
        'desc': '取单个 issue 详情（含最近 occurrence 抽样 + 处理事件流）。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {
                'fingerprint': _FINGERPRINT,
                'occurrence_limit': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': 100,
                    'description': '最近 occurrence 条数，默认 10',
                },
            },
            'required': ['fingerprint'],
        },
    },
    {
        'action': 'list_occurrences',
        'write': False,
        'scopes': [SCOPE_READ],
        'handler': _h_list_occurrences,
        'desc': '列某 issue 的原始 occurrence（深挖用，occurred_at 倒序 keyset 分页）。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {'fingerprint': _FINGERPRINT, 'limit': _LIMIT, 'cursor': _CURSOR},
            'required': ['fingerprint'],
        },
    },
    {
        'action': 'stats',
        'write': False,
        'scopes': [SCOPE_READ],
        'handler': _h_stats,
        'desc': '平台错误鸟瞰：按状态/严重度/来源聚合计数 + 总数。确定性读。',
        'schema': {'type': 'object', 'properties': {}},
    },
    {
        'action': 'update_issue',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_update_issue,
        'desc': (
            '挂 issue/PR 链接并把 issue 流转到 investigating（fixer 分身建了 GitHub issue/PR 后调用）。'
            '状态机校验非法流转报错；写事件留痕（actor=运维分身）。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'fingerprint': _FINGERPRINT,
                'issue_url': {'type': ['string', 'null'], 'description': 'GitHub issue 链接'},
                'pr_url': {'type': ['string', 'null'], 'description': 'GitHub PR 链接'},
                'note': {'type': ['string', 'null'], 'description': '处理备注（写进事件流）'},
            },
            'required': ['fingerprint'],
        },
    },
    {
        'action': 'resolve_issue',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_resolve_issue,
        'desc': (
            '结案 issue（resolved/skipped/wontfix）+ 处理结果。§6 必填校验：resolution_note 必填；'
            'code_fix 必填 fixed_in_version；duplicate 必填 duplicate_of_fingerprint。写事件留痕。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'fingerprint': _FINGERPRINT,
                'status': {'enum': ['resolved', 'skipped', 'wontfix'], 'description': '结案目标状态'},
                'resolution_type': {
                    'enum': ['code_fix', 'config_fix', 'duplicate', 'not_a_bug', 'external', 'cannot_reproduce'],
                    'description': '处理类型',
                },
                'resolution_note': {'type': 'string', 'minLength': 1, 'description': '处理说明（必填）'},
                'fixed_in_version': {'type': ['string', 'null'], 'description': 'code_fix 必填：修复所在版本'},
                'duplicate_of_fingerprint': {
                    'type': ['string', 'null'],
                    'description': 'duplicate 必填：主 issue 指纹',
                },
                'snooze_until': {'type': ['string', 'null'], 'description': 'ISO8601：skipped 时暂缓到某时间'},
            },
            'required': ['fingerprint', 'status', 'resolution_type', 'resolution_note'],
        },
    },
]


class _DiagTool(BaseTool):
    """diag 域单 struct + spec 派发（对齐 task.py / plan.py，避免同形态类样板）。"""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._action = spec['action']
        self._name = f'{NAMESPACE}.{spec["action"]}'
        self._desc = spec['desc']
        self._input_schema = spec['schema']
        self._write = bool(spec['write'])
        self._scopes: list[str] = list(spec['scopes'])
        self._handler: Handler = spec['handler']

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return NAMESPACE

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return self._desc

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def required_scopes(self) -> list[str]:
        # 读类 diag:read:all；写类 diag:manage。两者均命中特权前缀 diag: → G1 平台特权门统一判定。
        return list(self._scopes)

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        # G1 特权门 + G5 三态由 server.call_tool 统一判定（doc18 §4.1），工具内不二次校验。
        if self._write:
            async with async_db_session.begin() as db:
                return await self._handler(db, agent_context, arguments)
        async with async_db_session() as db:
            return await self._handler(db, agent_context, arguments)


DIAG_TOOLS: list[_DiagTool] = [_DiagTool(spec) for spec in _SPECS]
