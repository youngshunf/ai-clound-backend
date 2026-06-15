"""平台工具 · contact 域

`hasn.contact.list`：列出主人（owner）已建立连接的联系人，并补齐对方昵称/唤星号/类型/信任等级。
直接走 DAO 真实查询（不经请求作用域的 paging_data，故可在 MCP 调用栈外运行）。零 mock。
"""

from typing import Any

from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao
from backend.app.hasn_core import hasn_agents_dao, hasn_humans_dao
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100


async def _resolve_peer(db: Any, peer_id: str, peer_type: str) -> tuple[str, str]:
    """返回 (展示名, 唤星号)；人取 nickname，Agent 取 display_name。"""
    if peer_type == 'agent':
        agent = await hasn_agents_dao.get_by_hasn_id(db, hasn_id=peer_id)
        if agent:
            return (getattr(agent, 'display_name', '') or '', getattr(agent, 'star_id', '') or '')
    else:
        human = await hasn_humans_dao.get_by_hasn_id(db, hasn_id=peer_id)
        if human:
            return (getattr(human, 'nickname', '') or '', getattr(human, 'star_id', '') or '')
    return ('', '')


class ContactListTool(BaseTool):
    """获取联系人列表工具"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.contact.list'

    @property
    def namespace(self) -> str:
        return 'hasn.contact'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return '获取已建立连接的联系人列表（含对方昵称/唤星号/类型/信任等级）'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'limit': {
                    'type': 'integer',
                    'description': f'返回数量限制（默认 {_DEFAULT_LIMIT}）',
                    'minimum': 1,
                    'maximum': _MAX_LIMIT,
                }
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['contact:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        # 维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验。
        limit = min(max(int(arguments.get('limit', _DEFAULT_LIMIT)), 1), _MAX_LIMIT)
        async with async_db_session() as db:
            rows = await hasn_contacts_dao.list_contacts(
                db, owner_id=agent_context.owner_hasn_id, status='connected', limit=limit
            )
            contacts: list[dict[str, Any]] = []
            for row in rows:
                display_name, star_id = await _resolve_peer(db, row.peer_id, row.peer_type)
                contacts.append({
                    'contact_hasn_id': row.peer_id,
                    'peer_type': row.peer_type,
                    'display_name': display_name,
                    'star_id': star_id,
                    'relation_type': row.relation_type,
                    'trust_level': row.trust_level,
                    'status': row.status,
                })
            return {'contacts': contacts, 'total': len(contacts)}
