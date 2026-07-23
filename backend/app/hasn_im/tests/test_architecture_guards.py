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
