"""工作台 MCP 工具集（agent-scoped，供主脑调用）。

`hasn.workbench.briefing.publish`：主脑产出每日关注简报的**唯一**上行通道。入口**强校验**
BriefingDocument schema（设计 doc 04 §4），不合即返回校验错误让模型重试——绝不正则解析自由
文本拼简报（零 fake）。身份由认证决定：owner_id/agent_id 取自 Agent 凭证回填，不信任入参。
"""

from __future__ import annotations

import copy

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from backend.app.mcp.tools.base import BaseTool
from backend.common.log import log
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

# 统一挂在 /apps/<id> 前缀下的 AI-Native 应用 id（事实源：webui routes.tsx + 各 manifest entry_route）。
# 与 webui `src/lib/legacyRoute.ts::APP_ROOT_IDS` 保持一致；新增 /apps 应用时两处同步。
_APP_ROOT_IDS: frozenset[str] = frozenset(
    {
        'tasks',
        'community',
        'knowledge',
        'deck',
        'designsystem',
        'film',
        'design',
        'reel',
        'studio',
        'quant',
        'finance',
        'plan',
        'copilot',
        'creator',
        'growth',
        'publish',
    }
)


def _normalize_route(value: Any) -> Any:
    """把 legacy 内部路由前缀归一到 canonical `/apps/<id>`（与 webui legacyRoute 同规则）。

    APPS-* 统一路由后应用页都在 `/apps/<id>`，但主脑偶发照旧写 `/workbench/apps/deck`、裸 `/tasks/T-12`
    会 404。这里在**写入云端权威前**归一，让存库文档本身干净（webui 渲染侧也有兜底，双保险）。
    非字符串 / 非以 `/` 开头（`hasn://` / `http` / 相对路径）原样返回，保留 query/hash 后缀，幂等。
    """
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed.startswith('/'):
        return value
    positions = [pos for pos in (trimmed.find('?'), trimmed.find('#')) if pos != -1]
    cut = min(positions) if positions else -1
    path = trimmed if cut == -1 else trimmed[:cut]
    suffix = '' if cut == -1 else trimmed[cut:]
    # 1) 去早期外壳前缀 /workbench。
    if path == '/workbench':
        path = '/home'
    elif path.startswith('/workbench/'):
        path = path[len('/workbench') :]
    # 2) 裸应用根段（未带 /apps 前缀，首段是已知应用 id）补 /apps。
    segments = [seg for seg in path.split('/') if seg]
    if segments and segments[0] != 'apps' and segments[0] in _APP_ROOT_IDS:
        path = '/apps/' + '/'.join(segments)
    return path + suffix


def _fix_action_routes(action: Any) -> None:
    """就地归一单个 action 的 deep_link / route。"""
    if not isinstance(action, dict):
        return
    if 'deep_link' in action:
        action['deep_link'] = _normalize_route(action['deep_link'])
    if 'route' in action:
        action['route'] = _normalize_route(action['route'])


def _fix_action_list(actions: Any) -> None:
    if isinstance(actions, list):
        for action in actions:
            _fix_action_routes(action)


def _fix_focus_item(item: Any) -> None:
    """就地归一单个 focus_item 的 source.deep_link 与 actions。"""
    if not isinstance(item, dict):
        return
    source = item.get('source')
    if isinstance(source, dict) and 'deep_link' in source:
        source['deep_link'] = _normalize_route(source['deep_link'])
    _fix_action_list(item.get('actions'))


def _canonicalize_document_routes(document: dict[str, Any]) -> dict[str, Any]:
    """归一 BriefingDocument 里所有 deep_link / route（focus_items[].source/actions、plans[].actions）。

    返回新文档（不改入参）；只碰路由字段，其余原样透传（schema extra='allow'）。
    """
    doc = copy.deepcopy(document)
    focus_items = doc.get('focus_items')
    if isinstance(focus_items, list):
        for item in focus_items:
            _fix_focus_item(item)
    plans = doc.get('plans')
    if isinstance(plans, list):
        for plan in plans:
            if isinstance(plan, dict):
                _fix_action_list(plan.get('actions'))
    return doc


class PublishBriefingTool(BaseTool):
    """主脑发布每日关注简报（覆盖当日 period，写云端权威 hasn_workbench_briefing）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.workbench'

    @property
    def name(self) -> str:
        return 'hasn.workbench.briefing.publish'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def description(self) -> str:
        return (
            '发布今天的「每日关注简报」。你必须传入一份结构化 BriefingDocument（见 document schema），'
            '不要用自由文本——工作台只渲染这个结构。覆盖当日最新一份。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        action_schema = {
            'type': 'object',
            'properties': {
                'kind': {'type': 'string', 'enum': ['open_app', 'run_task', 'open_route', 'dismiss']},
                'label': {'type': 'string', 'description': '按钮文案'},
                'app_id': {'type': 'string', 'description': 'open_app：目标应用 id'},
                'deep_link': {'type': 'string', 'description': 'open_app：应用内深链'},
                'agent_id': {'type': 'string', 'description': 'run_task：执行分身（默认主脑）'},
                'prompt': {'type': 'string', 'description': 'run_task：派发提示词'},
                'skill_ids': {'type': 'array', 'items': {'type': 'string'}},
                'confirm': {'type': 'boolean', 'description': 'run_task：是否弹确认'},
                'route': {'type': 'string', 'description': 'open_route：客户端内部路由'},
            },
            'required': ['kind', 'label'],
        }
        focus_item_schema = {
            'type': 'object',
            'properties': {
                'item_id': {'type': 'string'},
                'category': {'type': 'string', 'enum': ['task', 'social', 'app', 'plan', 'risk']},
                'urgency': {'type': 'string', 'enum': ['high', 'medium', 'low']},
                'title': {'type': 'string'},
                'summary': {'type': 'string'},
                'source': {
                    'type': 'object',
                    'properties': {
                        'app_id': {'type': 'string'},
                        'ref': {'type': 'string'},
                        'deep_link': {'type': 'string'},
                    },
                },
                'evidence': {'type': 'array', 'items': {'type': 'string'}},
                'actions': {'type': 'array', 'items': action_schema},
            },
            'required': ['item_id', 'category', 'urgency', 'title'],
        }
        plan_item_schema = {
            'type': 'object',
            'properties': {
                'plan_id': {'type': 'string'},
                'title': {'type': 'string'},
                'horizon': {'type': 'string', 'enum': ['today', 'week']},
                'steps': {'type': 'array', 'items': {'type': 'string'}},
                'actions': {'type': 'array', 'items': action_schema},
            },
            'required': ['plan_id', 'title', 'horizon'],
        }
        return {
            'type': 'object',
            'properties': {
                'document': {
                    'type': 'object',
                    'description': 'BriefingDocument（owner_id/agent_id 由系统回填，无需填）',
                    'properties': {
                        'period': {'type': 'string', 'description': '覆盖周期 YYYY-MM-DD（缺省取当日）'},
                        'state': {'type': 'string', 'enum': ['generating', 'ready', 'failed'], 'default': 'ready'},
                        'summary': {'type': 'string', 'description': '一句话总览（Hero 副标题）'},
                        'focus_items': {'type': 'array', 'items': focus_item_schema},
                        'plans': {'type': 'array', 'items': plan_item_schema},
                    },
                    'required': ['summary', 'focus_items'],
                },
            },
            'required': ['document'],
        }

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.home.service.hasn_workbench_briefing_service import hasn_workbench_briefing_service

        document = arguments.get('document')
        if not isinstance(document, dict):
            return {'published': False, 'valid': False, 'reason': 'document 必填且须为对象'}

        # 写权威前归一 legacy 路由前缀（/workbench/apps/deck、裸 /tasks/... → /apps/...），
        # 让存库文档本身 canonical（避免旧简报按钮 404；webui 渲染侧另有兜底）。
        document = _canonicalize_document_routes(document)

        async with async_db_session() as db:
            try:
                row = await hasn_workbench_briefing_service.publish(
                    db=db,
                    owner_hasn_id=agent_context.owner_hasn_id,
                    agent_hasn_id=agent_context.hasn_id,
                    document=document,
                )
            except ValidationError as exc:
                # schema 不合：返回结构化错误让模型重试（零 fake，不落库）。
                log.info(f'briefing publish schema invalid by {agent_context.hasn_id}: {exc.error_count()} errors')
                return {
                    'published': False,
                    'valid': False,
                    'reason': 'BriefingDocument 校验失败，请按 schema 修正后重试',
                    'errors': exc.errors(include_url=False)[:10],
                }
            # 会话关闭前抓出纯值（避免 detached ORM 实例访问报错）。
            result = {
                'published': True,
                'valid': True,
                'period': row.period,
                'state': row.state,
                'briefing_id': row.document_json.get('briefing_id'),
            }
            await db.commit()
        return result


class PendingScanTool(BaseTool):
    """扫描主人名下各 AI-Native 应用的**未处理项**，聚合成结构化清单供主脑分诊派发。

    这是「主动去工作」的入口（设计 doc 05 §4）：主脑先 scan 拿到权威、不漏的未处理清单，
    再逐项分诊——① 能独立做的**直接派发任务/发起工作会话**；② 需主人决策的**在工作会话
    发提问卡**（hasn.session.ask）；③ 只能主人线下办的**才仅提醒**。绝不替主人拍板。
    """

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.workbench'

    @property
    def name(self) -> str:
        return 'hasn.workbench.pending.scan'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def required_scopes(self) -> list[str]:
        return ['workbench:pending:read']

    @property
    def description(self) -> str:
        return (
            '扫描主人名下各应用的未处理项（后端权威聚合，一次拿全、不漏）。返回 by_app 分组 + total + '
            'degraded（读取失败的应用，如实标注，绝不为其造项）。你据此分诊：能直接做的派任务/发起工作会话，'
            '需主人决策的发提问卡，只能主人线下办的才提醒。plan 只回逾期待办（未逾期的已有自动派发逻辑，勿重复）。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'apps': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '限定扫描的应用 id（如 ["task","plan"]）；缺省=全部已接入应用',
                },
                'limit_per_app': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': 50,
                    'description': '每应用返回明细条数上限（count 仍是真实总数），缺省 5',
                },
            },
        }

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.home.service.workbench_pending_aggregator_service import workbench_pending_aggregator

        owner = agent_context.owner_hasn_id
        if not owner:
            return {'error': 'owner 身份缺失，无法扫描未处理项'}

        apps = arguments.get('apps')
        apps = [str(a) for a in apps] if isinstance(apps, list) else None
        raw_limit = arguments.get('limit_per_app')
        limit = raw_limit if isinstance(raw_limit, int) and raw_limit > 0 else 5

        async with async_db_session() as db:
            result = await workbench_pending_aggregator.scan(
                db, owner_hasn_id=owner, apps=apps, limit_per_app=limit
            )
        return result.model_dump(mode='json')


WORKBENCH_TOOLS: list[type[BaseTool]] = [PublishBriefingTool, PendingScanTool]
