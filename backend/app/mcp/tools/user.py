"""平台工具 · user 域（P4-A / A1）

`hasn.user.search`：按唤星号精确 + 昵称前缀模糊搜索 HASN 用户（人/Agent），走真实 DAO 查询路径
（与 `api/v1/app/search.py` 同源），并附带从真实关系派生的可达性提示（维度②的轻量提示，
能否真正发消息/加联系最终由 message_router.check_relation_permission 在对应工具内判定）。

零 mock：直接查 humans/agents/contacts 真实表，无假数据。身份按 agent_hasn_id（G2：不使用 owner_user_id）。
"""

from typing import Any

from backend.app.hasn_im.application.provider import get_relation_gateway
from backend.app.hasn_core import hasn_agents_dao, hasn_humans_dao
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


class UserSearchTool(BaseTool):
    """搜索 HASN 用户（人/Agent），用于发起联系或发消息。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.user'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def name(self) -> str:
        return 'hasn.user.search'

    @property
    def description(self) -> str:
        return '按唤星号或昵称搜索 HASN 用户（人或 Agent），返回与你的现有关系与可达性提示'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': '唤星号（精确）或昵称前缀', 'minLength': 2, 'maxLength': 64},
                'limit': {
                    'type': 'integer',
                    'description': f'返回上限（默认 {_DEFAULT_LIMIT}）',
                    'minimum': 1,
                    'maximum': _MAX_LIMIT,
                },
            },
            'required': ['query'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['user:search']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        # 维度① 能力授权由 server.call_tool 三态统一判定（D3），此处不二次校验。
        query = str(arguments.get('query', '')).strip()
        if len(query) < 2:
            return {'results': [], 'total': 0, 'error': 'query 至少 2 个字符'}
        limit = min(max(int(arguments.get('limit', _DEFAULT_LIMIT)), 1), _MAX_LIMIT)

        # 搜索方的身份 = 发起搜索的 Agent（G2：用 hasn_id，不用 owner_user_id）
        self_hasn_id = agent_context.agent_hasn_id

        async with async_db_session() as db:
            results: list[dict[str, Any]] = []
            seen: set[str] = set()

            # 1) 唤星号精确匹配（含 # 查 agent，否则查 human）
            if '#' in query:
                agent = await hasn_agents_dao.get_by_star_id(db, query)
                if agent and agent.hasn_id != self_hasn_id:
                    results.append(self._agent_item(agent))
                    seen.add(agent.hasn_id)
            else:
                human = await hasn_humans_dao.get_by_star_id(db, query)
                if human and human.hasn_id != self_hasn_id:
                    results.append(self._human_item(human))
                    seen.add(human.hasn_id)

            # 2) 昵称前缀模糊匹配（humans）
            if len(results) < limit:
                humans = await hasn_humans_dao.search_by_name(
                    db, prefix=query, limit=limit - len(results) + 1, exclude_hasn_id=self_hasn_id
                )
                for h in humans:
                    if h.hasn_id in seen:
                        continue
                    results.append(self._human_item(h))
                    seen.add(h.hasn_id)
                    if len(results) >= limit:
                        break

            # 3) 真实关系 → 可达性提示（维度②轻量提示）
            for item in results:
                relation = await get_relation_gateway().resolve_effective_relation(
                    owner_hasn_id=self_hasn_id,
                    peer_hasn_id=item['hasn_id'],
                )
                status = relation.status if relation else None
                item['existing_relation'] = status
                item['can_message'] = status == 'connected'
                item['can_request'] = status is None

            return {'results': results, 'total': len(results)}

    @staticmethod
    def _human_item(human: Any) -> dict[str, Any]:
        return {
            'hasn_id': human.hasn_id,
            'star_id': human.star_id,
            'display_name': getattr(human, 'nickname', '') or '',
            'kind': 'human',
        }

    @staticmethod
    def _agent_item(agent: Any) -> dict[str, Any]:
        return {
            'hasn_id': agent.hasn_id,
            'star_id': agent.star_id,
            'display_name': getattr(agent, 'display_name', '') or '',
            'kind': 'agent',
        }
