"""R1-12 · CI 架构守卫（§0.1 单向依赖不变量的静态半边）。

三道红线，任一被打破即 CI 红：

1. **import guard**：`backend/app/**` 下**禁止新增**对 `message_router` / `ws_router` /
   `ws_delivery_bus` / `ws_node` 的直接 import——业务模块只能经
   `hasn_im.ports.ImGateway` / `RelationGateway` / `RealtimeGateway` 调通信域（§0.1）。
   白名单 = R0-02 存量导入方（带 owner + 删除期限），R1-05 各切片切到 port 后从白名单移除；
   **不得往白名单加新行**（加行=违背收编方向，评审拦）。

2. **legacy Redis key guard**：`backend/app/**` 禁止新增直接访问 IM 遗留 Redis key
   `hasn:node_*`、`hasn:entity_node`、`hasn:offline:*`、`hasn:push:*`。旧 key 访问必须
   限定在收口期间的兼容入口并持续清单化。

3. **DML guard**：`backend/app/**`（除 `hasn_im`/`hasn_sync` 自身 adapters）**禁止**直接
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

# —— import guard 白名单（R0-02 存量 + 授权包装）——
# 每项：相对 backend/ 的路径 → (owner, 删除期限/去向)。切到 ImGateway 后删除对应行。
_MESSAGE_ROUTER_ALLOW: dict[str, tuple[str, str]] = {
    # 授权收编点：通信域 application 自身允许直连现网 message_router 作收编封装（非业务模块，
    # 与「R0-02 业务存量」段区分；R2 消费者化后逐一退役，非「往白名单加业务行」）。
    'app/hasn_im/application/local_gateway.py': ('im-refactor', 'R2-04 后内联 route 逻辑，删本依赖'),
    'app/hasn_im/application/system_card_deliverer.py': ('im-refactor', 'R1-06 系统卡片投递收编点：R1-08 事务收口 / R2 消费者化后退役'),
    # R0-02 存量调用方（R1-05 各切 port 后逐个移除）
    'app/hasn_community/service/community_card_notifier.py': ('im-refactor', 'R1-05 slice③ 应用完成卡'),
    'app/hasn/api/v1/app/hasn_agents.py': ('im-refactor', 'R1-05 slice① MCP/agent'),
    'app/hasn/service/hasn_conversations_service.py': ('im-refactor', 'R1-05 slice⑥ 会话查询'),
    'app/hasn/service/hasn_group_service.py': ('im-refactor', 'R1-05 slice⑤ groups'),
    'app/hasn/service/inbound_release.py': ('im-refactor', 'R2-04 抑制放行事务化'),
    'app/hasn/service/owner_message_sync_service.py': ('im-refactor', 'R1-05 slice⑥ presence/查询'),
    'app/hasn/service/hasn_sessions_service.py': ('im-refactor', 'R1-05 slice① 会话/消息同步入口'),
    'app/mcp/ask_gate.py': ('im-refactor', 'R1-05 slice① MCP ask gate'),
    'app/mcp/tools/group.py': ('im-refactor', 'R1-05 slice⑤ groups'),
    'app/mcp/tools/message.py': ('im-refactor', 'R1-05 slice① MCP message'),
    'app/notification/service/notification_carrier.py': ('im-refactor', 'R1-06 已删 persist_message 旁路；剩 deliver_card_to_agent(route) 待 R1-05 slice② 切'),
}

_WS_ROUTER_ALLOW: dict[str, tuple[str, str]] = {
    # R0-02 / P0 统计的业务直连方，完成每片切片后对应行应移除
    'app/hasn/service/hasn_agents_service.py': ('im-refactor', 'R1-05 slice① MCP/agent'),
    'app/hasn/service/hasn_contacts_service.py': ('im-refactor', 'R1-05 slice④ 联系人关系事件'),
    'app/hasn/service/workspace_notification_subscriber.py': ('im-refactor', 'R1-05 slice② 通知通道'),
    'app/hasn/service/task_scheduler.py': ('im-refactor', 'R1-05 slice③ 后台任务'),
    'app/hasn/service/hasn_message_hub_service.py': ('im-refactor', 'R1-05 slice② 消息中枢'),
    'app/hasn/service/inbound_release.py': ('im-refactor', 'R1-05 slice⑥ 会话入口'),
    'app/hasn/service/sync_invalidate_service.py': ('im-refactor', 'R1-04 invalidate 直发，R1-05/2 收口'),
    'app/hasn/service/message_router.py': ('im-refactor', 'R1-04 兼容层收编'),
    'app/hasn/service/ws_delivery_bus.py': ('im-refactor', 'R1-02 routing 内核迁移中间层'),
    'app/hasn/api/v1/app/contacts.py': ('im-refactor', 'R1-05 slice④ 联系人 API'),
    'app/hasn_community/service/circle_service.py': ('im-refactor', 'R1-05 slice⑤ 社区组'),
    'app/hasn_community/service/community_service.py': ('im-refactor', 'R1-05 slice⑤ 社区与成员'),
}

_WS_DELIVERY_BUS_ALLOW: dict[str, tuple[str, str]] = {
    'app/hasn/service/ws_router.py': ('im-refactor', 'R1-02 兼容桥：后续迁移到 hasn_im.adapters.routing.delivery_bus'),
}

_WS_NODE_ALLOW: dict[str, tuple[str, str]] = {
    'app/hasn/api/router.py': ('im-refactor', 'R1-09 协议层净化后收进兼容 adapter'),
}

# legacy IM key 访问仅允许在这两处兼容入口；超过这两处即拦截收口偏差
_IM_LEGACY_REDIS_KEY_ALLOW: dict[str, tuple[str, str]] = {
    'app/hasn/service/ws_router.py': ('im-refactor', 'R2-01 迁出前保留统一收口点'),
    'app/hasn/service/ws_delivery_bus.py': ('im-refactor', 'R1-02 兼容层仍含 legacy key 语义注释'),
    'app/hasn_im/adapters/routing/redis_presence_store.py': ('im-refactor', 'R1-02 routing 收口入口统一 legacy key 所在'),
}
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


def _imported_modules(path: Path) -> set[str]:
    """返回该文件 import 的完整点分目标集合（Import + ImportFrom 两种形态都覆盖）。

    - `import a.b.c` → {'a.b.c'}
    - `from a.b import c` → {'a.b', 'a.b.c'}（后者覆盖 `from x.service import message_router`）
    - `from a.b.c import d` → {'a.b.c', 'a.b.c.d'}
    相对 import（level>0）无法静态解析包路径，跳过。
    """
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                mods.add(node.module)
                for alias in node.names:
                    mods.add(f'{node.module}.{alias.name}')
    return mods


def _files_importing(full_module: str) -> set[str]:
    """所有 import 了 full_module（精确点分路径）的 app 文件（相对 backend/ 路径）。"""
    hit: set[str] = set()
    for path in _iter_app_py_files():
        if full_module in _imported_modules(path):
            hit.add(_rel(path))
    return hit


def test_no_new_message_router_importers():
    """除白名单外，不得有新文件直接 import message_router（走 hasn_im.ports.ImGateway）。"""
    importers = _files_importing('backend.app.hasn.service.message_router')
    # message_router.py 自身不算
    importers.discard('app/hasn/service/message_router.py')
    offenders = sorted(importers - set(_MESSAGE_ROUTER_ALLOW))
    assert not offenders, (
        '发现新增的 message_router 直接 import（违背 §0.1）——请改经 '
        'backend.app.hasn_im.ports.ImGateway 调用，不要往白名单加行：\n  '
        + '\n  '.join(offenders)
    )


def test_message_router_whitelist_has_no_dead_entries():
    """白名单不得含已不再 import message_router 的死行（切到 port 后应移除白名单行）。"""
    importers = _files_importing('backend.app.hasn.service.message_router')
    importers.discard('app/hasn/service/message_router.py')
    dead = sorted(set(_MESSAGE_ROUTER_ALLOW) - importers)
    assert not dead, (
        '白名单存在已不再 import message_router 的死行，请删除（切到 port 后应移除白名单行）：\n  '
        + '\n  '.join(dead)
    )


def test_no_new_ws_node_importers():
    """除路由注册处外，不得有新文件直接 import ws_node（协议入口）。"""
    importers = _files_importing('backend.app.hasn.api.ws_node')
    importers.discard('app/hasn/api/ws_node.py')
    offenders = sorted(importers - set(_WS_NODE_ALLOW))
    assert not offenders, (
        '发现新增的 ws_node 直接 import（违背 §0.1）——协议帧处理应经 hasn_im 协议层：\n  '
        + '\n  '.join(offenders)
    )


def test_ws_node_whitelist_has_no_dead_entries():
    """白名单不得含已不再 import ws_node 的死行。"""
    importers = _files_importing('backend.app.hasn.api.ws_node')
    importers.discard('app/hasn/api/ws_node.py')
    dead = sorted(set(_WS_NODE_ALLOW) - importers)
    assert not dead, (
        '白名单存在已不再 import ws_node 的死行，请删除：\n  '
        + '\n  '.join(dead)
    )


def test_no_new_ws_router_importers():
    """除白名单外，不得有新文件直接 import ws_router（走 `hasn_im` ports）。"""
    importers = _files_importing('backend.app.hasn.service.ws_router')
    # ws_router.py 为定义方，不算消费者
    importers.discard('app/hasn/service/ws_router.py')
    offenders = sorted(importers - set(_WS_ROUTER_ALLOW))
    assert not offenders, (
        '发现新增的 ws_router 直接 import（违背 §0.1）——请改经 hasn_im.ports 调用，不要往白名单加行：\n  '
        + '\n  '.join(offenders)
    )


def test_ws_router_whitelist_has_no_dead_entries():
    """白名单不得含已不再 import ws_router 的死行（切到 port 后应移除白名单行）。"""
    importers = _files_importing('backend.app.hasn.service.ws_router')
    importers.discard('app/hasn/service/ws_router.py')
    dead = sorted(set(_WS_ROUTER_ALLOW) - importers)
    assert not dead, (
        '白名单存在已不再 import ws_router 的死行，请删除：\n  '
        + '\n  '.join(dead)
    )


def test_no_new_ws_delivery_bus_importers():
    """除白名单外，不得有新文件直接 import ws_delivery_bus（禁止扩散私有字典依赖）。"""
    importers = _files_importing('backend.app.hasn.service.ws_delivery_bus')
    # ws_delivery_bus.py 为定义方，不算消费者
    importers.discard('app/hasn/service/ws_delivery_bus.py')
    offenders = sorted(importers - set(_WS_DELIVERY_BUS_ALLOW))
    assert not offenders, (
        '发现新增的 ws_delivery_bus 直接 import（违背 §0.1）：\n  '
        + '\n  '.join(offenders)
    )


def test_ws_delivery_bus_whitelist_has_no_dead_entries():
    """白名单不得含已不再 import ws_delivery_bus 的死行。"""
    importers = _files_importing('backend.app.hasn.service.ws_delivery_bus')
    importers.discard('app/hasn/service/ws_delivery_bus.py')
    dead = sorted(set(_WS_DELIVERY_BUS_ALLOW) - importers)
    assert not dead, (
        '白名单存在已不再 import ws_delivery_bus 的死行，请删除：\n  '
        + '\n  '.join(dead)
    )


def test_no_new_legacy_im_redis_key_access():
    """除白名单外，不得有新文件直接访问 legacy IM Redis key（收口前先走守卫）。"""
    offenders: list[str] = []
    for path in _iter_app_py_files():
        rel = _rel(path)
        if rel.startswith('app/hasn_im/tests/'):
            continue
        if any(rel.startswith(prefix) for prefix in _DML_ALLOW_PREFIXES):
            continue
        if rel in _IM_LEGACY_REDIS_KEY_ALLOW:
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if _IM_LEGACY_REDIS_KEY_RE.search(content):
            offenders.append(rel)
    assert not offenders, (
        '发现新增直接访问 legacy IM Redis key（应迁移到 hasn_im.adapters.routing）：\n  '
        + '\n  '.join(sorted(set(offenders)))
    )


def test_no_direct_im_sync_model_dml_outside_adapters():
    """业务模块不得直接 import hasn_im/hasn_sync 的 ORM 模型（写一律经 port/append_event）。

    当前新模型未建（R2-01/R2-07），保护集为空 → 放行；R2 建模后自动生效。
    """
    offenders: list[str] = []
    for path in _iter_app_py_files():
        rel = _rel(path)
        if any(rel.startswith(prefix) for prefix in _DML_ALLOW_PREFIXES):
            continue
        mods = _imported_modules(path)
        if any(mod.startswith(prefix) for mod in mods for prefix in _PROTECTED_MODEL_PREFIXES):
            offenders.append(rel)
    assert not offenders, (
        '发现业务模块直接 import 受保护的 IM/sync ORM 模型（应经 port/append_event 写）：\n  '
        + '\n  '.join(sorted(offenders))
    )
