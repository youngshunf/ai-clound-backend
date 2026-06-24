"""平台工具 · contact 域

- `hasn.contact.list`：列出主人（owner）已建立连接的联系人，补齐对方昵称/唤星号/类型/信任等级。
  可选 `query` 子串过滤、`include_agents` 带出好友名下的 agent。
- `hasn.contact.search`：按昵称/唤星号/备注名搜索主人的联系人（好友 + 好友名下的 agent）。
  返回项的 `contact_hasn_id` 可直接用作 `hasn.message.send` 的 `to`，打通"搜联系人→发消息"闭环。

两者均直接走 DAO 真实查询（不经请求作用域的 paging_data，故可在 MCP 调用栈外运行）。零 mock。
"""

from typing import Any

from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao
from backend.app.hasn_core import hasn_agents_dao, hasn_humans_dao
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
# search / include_agents 需先取一批联系人再在内存过滤/展开；单主人联系人量级很小，取足够大的窗口即可。
_FETCH_CAP = 500


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


async def _contact_dict(db: Any, row: Any) -> dict[str, Any]:
    """把一条 HasnContacts 关系边整理成对外联系人字典。"""
    display_name, star_id = await _resolve_peer(db, row.peer_id, row.peer_type)
    return {
        'contact_hasn_id': row.peer_id,
        'peer_type': row.peer_type,
        'display_name': display_name,
        'star_id': star_id,
        'relation_type': row.relation_type,
        'trust_level': row.trust_level,
        'status': row.status,
    }


def _matches(query: str, contact: dict[str, Any], row: Any) -> bool:
    """按昵称/唤星号/本地备注名子串匹配（不分大小写）；query 为空则全命中。"""
    if not query:
        return True
    haystack = ' '.join(filter(None, [
        contact.get('display_name') or '',
        contact.get('star_id') or '',
        getattr(row, 'nickname', '') or '',
    ])).lower()
    return query in haystack


async def _friend_agents(db: Any, friend_hasn_id: str, friend_name: str) -> list[dict[str, Any]]:
    """列出某 human 好友名下的 active agent（用于"给好友的 agent 发消息"）。"""
    agents = await hasn_agents_dao.get_active_agents_by_owner(db, owner_hasn_id=friend_hasn_id)
    return [{
        'contact_hasn_id': getattr(a, 'hasn_id', '') or '',
        'peer_type': 'agent',
        'display_name': getattr(a, 'display_name', '') or '',
        'star_id': getattr(a, 'star_id', '') or '',
        'profession': getattr(a, 'profession', '') or '',
        'relation_type': 'friend_agent',
        'owner_hasn_id': friend_hasn_id,
        'owner_display_name': friend_name,
        'trust_level': None,
        'status': 'active',
    } for a in agents]


async def _collect_contacts(
    db: Any,
    owner_hasn_id: str,
    *,
    query: str = '',
    include_agents: bool = False,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """统一收集逻辑：取连接关系 → 按 query 过滤 → 可选展开 human 好友名下 agent → 去重截断。"""
    q = (query or '').strip().lower()
    rows = await hasn_contacts_dao.list_contacts(
        db, owner_id=owner_hasn_id, status='connected', limit=_FETCH_CAP,
    )
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for row in rows:
        contact = await _contact_dict(db, row)
        if not _matches(q, contact, row):
            continue
        cid = contact['contact_hasn_id']
        if cid and cid not in seen:
            seen.add(cid)
            results.append(contact)
        if include_agents and row.peer_type == 'human':
            for agent in await _friend_agents(db, row.peer_id, contact['display_name']):
                aid = agent['contact_hasn_id']
                if aid and aid not in seen:
                    seen.add(aid)
                    results.append(agent)
    return results[:limit]


def _coerce_limit(value: Any) -> int:
    try:
        return min(max(int(value), 1), _MAX_LIMIT)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT


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
        return (
            '获取已建立连接的联系人列表（含对方昵称/唤星号/类型/信任等级；'
            '可选 query 过滤、include_agents 带出好友名下 agent）'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': '可选：按昵称/唤星号/备注名子串过滤（不分大小写）',
                },
                'include_agents': {
                    'type': 'boolean',
                    'description': '可选：为每个 human 好友带出其名下 active agent（默认 false）',
                },
                'limit': {
                    'type': 'integer',
                    'description': f'返回数量限制（默认 {_DEFAULT_LIMIT}）',
                    'minimum': 1,
                    'maximum': _MAX_LIMIT,
                },
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['contact:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        # 维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验。
        limit = _coerce_limit(arguments.get('limit', _DEFAULT_LIMIT))
        async with async_db_session() as db:
            contacts = await _collect_contacts(
                db,
                agent_context.owner_hasn_id,
                query=str(arguments.get('query') or ''),
                include_agents=bool(arguments.get('include_agents')),
                limit=limit,
            )
            return {'contacts': contacts, 'total': len(contacts)}


class ContactSearchTool(BaseTool):
    """按昵称/唤星号搜索联系人（好友 + 好友名下 agent）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.contact.search'

    @property
    def namespace(self) -> str:
        return 'hasn.contact'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '按昵称/唤星号/备注名搜索主人的联系人（好友及好友名下的 agent）。'
            '返回项的 contact_hasn_id 可直接用作 hasn.message.send 的 to。'
            'include_agents 默认 true：匹配到的 human 好友会一并带出其名下 active agent，便于给"好友的 agent"发消息。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': '搜索词：按对方昵称/唤星号/本地备注名子串匹配（不分大小写）',
                },
                'include_agents': {
                    'type': 'boolean',
                    'description': '是否一并带出匹配到的 human 好友名下的 active agent（默认 true）',
                },
                'limit': {
                    'type': 'integer',
                    'description': f'返回数量限制（默认 {_DEFAULT_LIMIT}）',
                    'minimum': 1,
                    'maximum': _MAX_LIMIT,
                },
            },
            'required': ['query'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['contact:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        # 维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验。
        query = str(arguments.get('query') or '').strip()
        limit = _coerce_limit(arguments.get('limit', _DEFAULT_LIMIT))
        include_agents = bool(arguments.get('include_agents', True))
        if not query:
            return {'contacts': [], 'total': 0, 'query': query}
        async with async_db_session() as db:
            contacts = await _collect_contacts(
                db,
                agent_context.owner_hasn_id,
                query=query,
                include_agents=include_agents,
                limit=limit,
            )
            return {'contacts': contacts, 'total': len(contacts), 'query': query}
