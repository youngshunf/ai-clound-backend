"""96号施工的 CI 架构守卫（§0.1 单向依赖不变量的静态半边）。

三道红线，任一被打破即 CI 红：

1. **遗留模块清除守卫**：旧 IM WebSocket/消息模块必须物理删除；`backend/app/**`
   既不能静态 import，也不能借 `importlib` 动态加载它们。业务模块只能经
   `hasn_im` 的 application / ports 调通信域。
2. **legacy Redis key guard**：除 `hasn_im.adapters.routing` 外，`backend/app/**` 禁止
   直接访问 `hasn:node_*`、`hasn:entity_node`、`hasn:offline:*`、`hasn:push:*`。
3. **DML guard**：`backend/app/**`（除 `hasn_im`/`hasn_sync` 自身 adapters）禁止直接
   import `hasn_im.model` / `hasn_sync.model` 的 ORM 实体做写入——IM/sync 写一律经 port /
   `append_event`。当前新模型未建（R2-01/R2-07），保护集为空、守卫自然放行，R2 建模后
   自动生效（无需回来打开开关）。

守卫是纯静态（AST 扫描），不依赖 DB/网络，随普通 pytest 恒跑。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# backend 包根（本文件 = backend/app/hasn_im/tests/test_architecture_guards.py）
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_APP_ROOT = _BACKEND_ROOT / 'app'

# 路径拆分构造，避免简单全文检索把守卫的检测目标误判为生产依赖。
_LEGACY_SERVICE_PACKAGE = 'backend.app.hasn.service'
_LEGACY_API_PACKAGE = 'backend.app.hasn.api'
_LEGACY_IM_MODULES = (
    *tuple(f'{_LEGACY_SERVICE_PACKAGE}.{name}' for name in ('message_router', 'ws_router', 'ws_delivery_bus')),
    f'{_LEGACY_API_PACKAGE}.ws_node',
)
_LEGACY_IM_FILES = tuple(
    f'app/hasn/{directory}/{name}.py'
    for directory, names in (
        ('service', ('message_router', 'ws_router', 'ws_delivery_bus')),
        ('api', ('ws_node',)),
    )
    for name in names
)

_IM_LEGACY_REDIS_KEY_RE = re.compile(r"hasn:(?:node_conn|node_generation|node_entities|node_alive|entity_node|offline:|push:)")

# —— DML guard：受保护的新 IM/sync 模型模块前缀（R2 建模后填充生效）——
_PROTECTED_MODEL_PREFIXES = (
    'backend.app.hasn_im.model',
    'backend.app.hasn_sync.model',
)
# 允许直接 import 受保护模型的目录（各域自身 adapters/tests + migration 脚本）
_DML_ALLOW_PREFIXES = (
    'app/hasn_im/adapters/',
    'app/hasn_im/tests/',
    'app/hasn_sync/adapters/',
    'app/hasn_sync/tests/',
)


def _iter_app_py_files():
    for path in sorted(_APP_ROOT.rglob('*.py')):
        yield path


def _rel(path: Path) -> str:
    return str(path.relative_to(_BACKEND_ROOT))


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _imported_modules(path: Path) -> set[str]:
    """返回该文件 import 的完整点分目标集合（含 `from x import y`）。"""
    tree = _parse(path)
    if tree is None:
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f'{node.module}.{alias.name}' for alias in node.names)
    return modules


def _string_literals(path: Path) -> set[str]:
    """返回 AST 中的字符串常量，用于拦截 importlib 的动态加载。"""
    tree = _parse(path)
    if tree is None:
        return set()
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_legacy_im_modules_are_physically_deleted():
    """硬切换后不得保留同名遗留文件，防止路由注册回退到旧实现。"""
    remaining = sorted(relative for relative in _LEGACY_IM_FILES if (_BACKEND_ROOT / relative).exists())
    assert not remaining, '遗留 IM 文件尚未删除：\n  ' + '\n  '.join(remaining)


def test_legacy_im_modules_cannot_be_loaded():
    """静态 import 与字符串动态加载都不得重新引用已删除的遗留模块。"""
    offenders: list[str] = []
    for path in _iter_app_py_files():
        modules = _imported_modules(path)
        strings = _string_literals(path)
        referenced = sorted(module for module in _LEGACY_IM_MODULES if module in modules or module in strings)
        if referenced:
            offenders.append(f'{_rel(path)}: {", ".join(referenced)}')
    assert not offenders, (
        '发现对已删除遗留 IM 模块的引用（包括 importlib 动态加载）：\n  '
        + '\n  '.join(offenders)
    )


def test_common_socketio_has_no_legacy_im_handlers():
    """通用 Socket.IO 只承载传统业务事件，不得重新接管 HASN IM。"""
    actions = (_BACKEND_ROOT / 'common/socketio/actions.py').read_text(encoding='utf-8')
    server = (_BACKEND_ROOT / 'common/socketio/server.py').read_text(encoding='utf-8')
    for token in ('hasn_message', 'hasn_read', 'hasn_ping', 'hasn_message_push'):
        assert token not in actions, f'通用 Socket.IO actions 仍注册旧 IM 事件：{token}'
    for token in ('hasn:offline:', 'hasn:ws:sid2id:', 'hasn_message_push'):
        assert token not in server, f'通用 Socket.IO server 仍保留旧 IM 在线路由：{token}'


def test_legacy_im_redis_key_access_is_limited_to_routing_adapters():
    """遗留 Redis key 只能留在 `hasn_im.adapters.routing` 的迁移实现内。"""
    offenders: list[str] = []
    for path in _iter_app_py_files():
        rel = _rel(path)
        if rel.startswith('app/hasn_im/tests/'):
            continue
        if any(rel.startswith(prefix) for prefix in _DML_ALLOW_PREFIXES):
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if _IM_LEGACY_REDIS_KEY_RE.search(content):
            offenders.append(rel)
    assert not offenders, (
        '发现直接访问 legacy IM Redis key（应迁移到 hasn_im.adapters.routing）：\n  '
        + '\n  '.join(sorted(set(offenders)))
    )


def test_no_direct_im_sync_model_dml_outside_adapters():
    """业务模块不得直接 import hasn_im/hasn_sync ORM 模型（写一律经 port/append_event）。"""
    offenders: list[str] = []
    for path in _iter_app_py_files():
        rel = _rel(path)
        if any(rel.startswith(prefix) for prefix in _DML_ALLOW_PREFIXES):
            continue
        modules = _imported_modules(path)
        if any(module.startswith(prefix) for module in modules for prefix in _PROTECTED_MODEL_PREFIXES):
            offenders.append(rel)
    assert not offenders, (
        '发现业务模块直接 import 受保护的 IM/sync ORM 模型（应经 port/append_event 写）：\n  '
        + '\n  '.join(sorted(offenders))
    )


def test_message_send_chain_only_appends_integration_event():
    """发送主链不得再直接写 Sync 或调用实时网关。"""
    path = _BACKEND_ROOT / 'app/hasn_im/application/message_service.py'
    tree = _parse(path)
    assert tree is not None
    route = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'route_message'
    )
    source = ast.get_source_segment(
        path.read_text(encoding='utf-8'),
        route,
    )
    assert source is not None
    for forbidden in (
        'SqlAlchemySyncAppender',
        '_fanout_message_new',
        '_flush_pushes',
        '_get_realtime_gateway',
    ):
        assert forbidden not in source, f'route_message 仍包含旧发送旁路：{forbidden}'
    assert source.count('_append_message_committed_event(') == 2
    assert 'if not conversation_id:' in source
    assert 'HASN_IM_SCHEMA_CUTOVER' not in source


def test_permission_audit_uses_isolated_python_transaction():
    """best-effort 审计失败不得把 IM 消息事务置为 aborted。"""
    path = _BACKEND_ROOT / 'app/hasn/service/permission_engine.py'
    source = path.read_text(encoding='utf-8')
    assert 'python_backend_db_session.begin()' in source
    assert 'hasn_audit_log_service.append(db=audit_db' in source


def test_relation_gateway_never_falls_back_to_python_session():
    """关系写适配器不得回落普通 Python 会话绕过 IM role。"""
    path = _BACKEND_ROOT / 'app/hasn_im/adapters/sqlalchemy_relation_gateway.py'
    source = path.read_text(encoding='utf-8')
    assert 'async_db_session' not in source
    assert 'im_service_db_session' in source


def test_relation_writes_are_closed_behind_gateway():
    """联系人业务入口不得再直接调用 DAO/service 写方法。"""
    business_paths = (
        _BACKEND_ROOT / 'app/hasn/api/v1/app/contacts.py',
        _BACKEND_ROOT / 'app/hasn/service/inbound_gatekeeper.py',
        _BACKEND_ROOT / 'app/hasn/service/hasn_agents_service.py',
        _BACKEND_ROOT / 'app/hasn/service/hasn_auth.py',
        _BACKEND_ROOT / 'app/mcp/tools/contact.py',
        _BACKEND_ROOT / 'app/mcp/tools/message.py',
    )
    forbidden = (
        'HasnContactsService.request_contact(',
        'HasnContactsService.remove_contact(',
        'hasn_contacts_dao.upsert_connected(',
        'hasn_contact_requests_dao.mark_accepted(',
        'hasn_contact_requests_dao.mark_rejected(',
        'hasn_contact_requests_dao.mark_withdrawn(',
        'pg_insert(HasnContacts)',
        'HasnContacts(',
    )
    offenders: list[str] = []
    for path in business_paths:
        source = path.read_text(encoding='utf-8')
        for token in forbidden:
            if token in source:
                offenders.append(f'{_rel(path)}: {token}')
    assert not offenders, '关系业务仍有绕过 RelationGateway 的写点：\n  ' + '\n  '.join(offenders)


def test_legacy_inbound_release_module_is_deleted():
    """抑制放行只能经 ImGateway，旧直写 Sync/Realtime 模块必须物理删除。"""
    path = _BACKEND_ROOT / 'app/hasn/service/inbound_release.py'
    assert not path.exists(), '旧 inbound_release 仍存在，可绕过 ImGateway 放行'


def test_generic_relation_admin_routes_are_read_only():
    """管理端通用 contacts/request CRUD 只能保留只读运营查询。"""
    for relative in (
        'app/hasn/api/v1/admin/hasn_contacts.py',
        'app/hasn/api/v1/admin/hasn_contact_requests.py',
    ):
        source = (_BACKEND_ROOT / relative).read_text(encoding='utf-8')
        for method in ('post', 'put', 'patch', 'delete'):
            assert f'@router.{method}(' not in source, f'{relative} 仍暴露 {method.upper()} 写路由'


def test_agent_generic_contacts_route_is_disabled_after_cutover():
    """R3 切换后 Agent 通用联系人 CRUD 不得注册。"""
    source = (_BACKEND_ROOT / 'app/hasn/api/router.py').read_text(encoding='utf-8')
    route_path = _BACKEND_ROOT / 'app/hasn/api/v1/agent/hasn_contacts.py'
    assert 'agent_hasn_contacts_router' not in source
    assert not route_path.exists()
