"""身份投影切片守卫（`docs/.../02-身份投影切片施工清单.md` I0）。

**规则**：20 个 AI-Native 应用模块的业务代码不得从 `backend.app.hasn_core` 直接
import 平台身份的 ORM 模型（`HasnHumans` / `HasnAgents`）或 DAO
（`hasn_humans_dao` / `hasn_agents_dao`）——这些是可变实现细节，应用侧只应经
`IdentityFacade`（`identity.get_human(...)` / `identity.ref_agent(...)` 等）拿只读
投影（`HumanRef` / `AgentRef`）。平台模块自身（`hasn`/`hasn_core`/`hasn_im` 等）
不在本守卫范围——它们本就是身份的权威实现方，用 ORM 属正常。

**起步以白名单兜底**：清单 I0 阶段先把当时仍在违规的文件整批列入 `_WHITELIST`，
守卫立即为绿；每完成一批迁移就从白名单删除对应文件，**白名单只减不增**。守卫用
「实际违规文件集合 == 白名单集合」精确比对（不是子集判断）：文件一旦被迁移干净但
忘记从白名单摘除会被本守卫抓出来（防止白名单腐化、长期虚假宽松）；反过来新增
违规而未登记也会被抓出来（防止悄悄退化）。

**守卫自身的有效性**由 `test_extract_banned_identity_imports_*` 系列覆盖：构造一段
"应当违规"的样例源码，断言扫描函数真的识别出来；再构造一段"改用 façade 后不应违规"
的样例，断言不会被误判——避免正则/AST 逻辑改错后守卫静默失效（长期沦为空转）。
"""

from __future__ import annotations

import ast

from pathlib import Path

import backend.app as backend_app

# 平台核心身份契约里"应用侧禁止直接 import"的四个符号。
# 只读投影 `HumanRef`/`AgentRef`/`IdentityFacade`/`identity` 不受限，
# 这正是应用侧应当迁移到的目标接缝。
_BANNED_SYMBOLS = frozenset({'HasnHumans', 'HasnAgents', 'hasn_humans_dao', 'hasn_agents_dao'})

# 身份 façade 的实现落点。应用侧只允许从这里（或其 `__init__` 重导出的
# `backend.app.hasn_core`）import；本守卫盯的正是"从这两个模块 import 禁用符号"。
_FACADE_MODULES = frozenset({'backend.app.hasn_core', 'backend.app.hasn_core.identity'})

# 施工清单 §2.2 认定的 20 个 AI-Native 应用模块（平台模块 hasn/hasn_core/hasn_im/
# hasn_sync/admin/billing/notification/mcp/home/marketplace/hasn_client/
# external_mcp/hasn_release/hasn_diag 等不在其内——它们是身份的权威实现方）。
_APP_MODULES = (
    'hasn_community',
    'hasn_imagelab',
    'hasn_finance',
    'hasn_deck',
    'hasn_plan',
    'hasn_reel',
    'hasn_film',
    'hasn_publish',
    'hasn_studio',
    'hasn_creator',
    'hasn_growth',
    'hasn_design',
    'hasn_designsystem',
    'hasn_copilot',
    'hasn_quant',
    'hasn_computer_use',
    'hasn_stock',
    'hasn_task',
    'hasn_knowledge',
    'hasn_project',
)

# I0 起步白名单：2026-08-06 施工前实测的 35 个违规文件全量登记，守卫从此刻起立即为绿。
# 随 I2/I3/I4 每完成一批迁移就从这里删除对应条目——**白名单只减不增**，见文件顶部说明。
_WHITELIST = frozenset({
    'hasn_community/api/v1/app/community.py',
    'hasn_community/api/v1/app/community_ext.py',
    'hasn_community/api/v1/open/community_ext.py',
    'hasn_community/service/community_card_notifier.py',
    'hasn_community/service/settings_service.py',
    'hasn_community/service/circle_service.py',
    'hasn_community/service/community_cards.py',
    'hasn_community/service/notification_service.py',
    'hasn_community/service/community_service.py',
    'hasn_community/service/admin_query_service.py',
    'hasn_community/service/doc_service.py',
    'hasn_deck/api/v1/app/deck.py',
    'hasn_plan/api/v1/app/plan.py',
    'hasn_plan/service/plan_authz.py',
    'hasn_publish/api/v1/app/site.py',
    'hasn_studio/service/media_credentials.py',
    'hasn_creator/service/creator_service.py',
    'hasn_creator/service/scope_context.py',
    'hasn_growth/service/growth_profile_service.py',
    'hasn_growth/service/scope_context.py',
    'hasn_growth/service/form_service.py',
    'hasn_growth/service/project_lead_service.py',
    'hasn_growth/service/business_service.py',
    'hasn_growth/service/outreach_service.py',
    'hasn_designsystem/api/v1/app/designsystem.py',
    'hasn_copilot/api/v1/app/meetings.py',
    'hasn_copilot/api/v1/app/copilot.py',
    'hasn_copilot/service/meetings_service.py',
    'hasn_copilot/service/copilot_service.py',
    'hasn_task/api/v1/app/skill_bundle.py',
    'hasn_task/api/v1/app/task.py',
    'hasn_task/service/builtin_seeding_service.py',
    'hasn_knowledge/api/v1/app/knowledge.py',
    'hasn_project/api/v1/app/_common.py',
    'hasn_project/service/project_app_service.py',
})


def _iter_business_py_files(module_root: Path) -> list[Path]:
    """列出一个应用模块下的"业务代码" .py 文件：排除 tests 目录与 test_*.py。"""
    files: list[Path] = []
    for path in sorted(module_root.rglob('*.py')):
        relative_parts = path.relative_to(module_root).parts
        if 'tests' in relative_parts or 'test' in relative_parts:
            continue
        if path.name.startswith('test_'):
            continue
        files.append(path)
    return files


def extract_banned_identity_imports(source: str) -> frozenset[str]:
    """解析一段 Python 源码，返回其中从身份 façade 落点 import 的禁用符号集合。

    用 AST 而非正则：既能挡住 `from backend.app.hasn_core import HasnAgents`，
    也能挡住藏在函数体内部的局部 import（本仓真实存在——`discover_peers` 曾在
    函数体内 `from backend.app.hasn_core import hasn_agents_dao, hasn_humans_dao`），
    正则很容易漏掉后者。`ast.walk` 遍历整棵树，不管 import 语句嵌在哪一层。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in _FACADE_MODULES:
            continue
        for alias in node.names:
            if alias.name in _BANNED_SYMBOLS:
                found.add(alias.name)
    return frozenset(found)


def _scan_violations() -> dict[str, frozenset[str]]:
    """扫描全部 20 个应用模块，返回 {模块内相对路径: 违规符号集合}（仅含真违规文件）。"""
    app_root = Path(backend_app.__file__).resolve().parent
    violations: dict[str, frozenset[str]] = {}
    for module_name in _APP_MODULES:
        module_root = app_root / module_name
        if not module_root.is_dir():
            # 模块目录缺失属环境/仓状态问题，留给别的守卫处理，这里不因此误报。
            continue
        for path in _iter_business_py_files(module_root):
            banned = extract_banned_identity_imports(path.read_text(encoding='utf-8'))
            if banned:
                relative = str(path.relative_to(app_root))
                violations[relative] = banned
    return violations


# ==================== 守卫自身有效性 ====================


def test_extract_banned_identity_imports_detects_direct_import() -> None:
    """样例：顶层直接 import 禁用符号 → 必须被识别为违规（守卫没失效）。"""
    source = 'from backend.app.hasn_core import HasnAgents, HasnHumans\n'
    assert extract_banned_identity_imports(source) == {'HasnAgents', 'HasnHumans'}


def test_extract_banned_identity_imports_detects_nested_function_import() -> None:
    """样例：import 藏在函数体内部（本仓 discover_peers 真实出现过的写法）→ 仍需识别。"""
    source = (
        'async def f(db):\n'
        '    from backend.app.hasn_core import hasn_agents_dao, hasn_humans_dao\n'
        '    return hasn_agents_dao, hasn_humans_dao\n'
    )
    assert extract_banned_identity_imports(source) == {'hasn_agents_dao', 'hasn_humans_dao'}


def test_extract_banned_identity_imports_detects_dao_from_identity_submodule() -> None:
    """样例：直接从 `hasn_core.identity` 子模块 import（绕开 `__init__` 重导出）同样违规。"""
    source = 'from backend.app.hasn_core.identity import hasn_humans_dao\n'
    assert extract_banned_identity_imports(source) == {'hasn_humans_dao'}


def test_extract_banned_identity_imports_ignores_facade_projection() -> None:
    """样例：改用只读投影 façade 后不应再被判违规（否则迁移完了守卫还拦，逼人反悔迁移）。"""
    source = 'from backend.app.hasn_core import AgentRef, HumanRef, IdentityFacade, identity\n'
    assert extract_banned_identity_imports(source) == frozenset()


def test_extract_banned_identity_imports_ignores_unrelated_module() -> None:
    """样例：同名符号来自其他模块（非身份 façade 落点）不应被误伤。"""
    source = 'from some.other.module import HasnAgents\n'
    assert extract_banned_identity_imports(source) == frozenset()


# ==================== 真实仓库扫描 ====================


def test_ai_native_apps_do_not_import_platform_identity_orm() -> None:
    """20 个应用模块的业务代码，违规文件集合必须与白名单精确一致。

    精确比对（非子集）是为了让白名单保持诚实：
    - 多出的违规（不在白名单）→ 有人新写了直接 import，本条炸出来拦截退化；
    - 白名单里但已不再违规的文件 → 说明迁移已完成却忘了从白名单摘除，本条同样炸出来，
      逼迁移者做「随手清白名单」这最后一步，白名单才能真正随批次收敛到空（I0-I4 完成后
      仅剩 I5 的 2 处企业 JOIN + 本次新识别的 community_service.py 身份目录搜索集群）。
    """
    violations = _scan_violations()
    actual_violating_files = frozenset(violations)
    assert actual_violating_files == _WHITELIST, (
        f'身份投影导入白名单已过期。\n'
        f'白名单多出（已修好，请从 _WHITELIST 删除）：{sorted(_WHITELIST - actual_violating_files)}\n'
        f'扫描新增（未登记的违规，请迁移或登记原因后加入 _WHITELIST）：'
        f'{sorted(actual_violating_files - _WHITELIST)}\n'
        f'当前扫描到的完整违规详情：{ {k: sorted(v) for k, v in violations.items()} }'
    )
