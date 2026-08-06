"""hasn_core façade 反向 import 守卫（架构候选③ P4）。

P1（身份 façade）+ P2（应用平台服务 façade）已把两条**深接缝**完全收敛进 `hasn_core`：
- 身份 DAO / 模型 → `from backend.app.hasn_core import …`
- 应用平台服务 / 对外 schema → `from backend.app.hasn_core.app_platform import …`

本守卫**锁死回归**：禁止 `app/hasn` 与 `app/hasn_core` 之外的任何模块再直接
`from backend.app.hasn.<crud|model|service|schema>.…` 抠这两条接缝已收编的具体内部符号。
新代码若想用身份或应用平台能力，必须走 façade。

> 注意：本守卫**只**守这两条**已收编**的接缝，不是「禁止一切 `from backend.app.hasn.`」——
> 平台原语（conversations / messages / contacts / sessions / nodes / ws_router / asset /
> sync / audit / notifications 等）仍是平台核心，尚未建 façade，故**不在**禁列里（见设计提案
> P3 长尾，经评估为浅接缝、暂不收敛）。`App` 数据类亦不在禁列——它被各应用 manifest
> 在子系统构建期消费，属应用平台内部，经定义处直接 import（façade 化会成环，见 P2）。
"""

from __future__ import annotations

import re

from pathlib import Path

# 仓库根：本文件在 backend/tests/hasn_core/ 下，向上三层到 hasn-cloud-backend/
_REPO_ROOT = Path(__file__).resolve().parents[3]
_APP_DIR = _REPO_ROOT / 'backend' / 'app'

# 豁免：façade 的实现侧（app/hasn）与 façade 本身（app/hasn_core）。
_EXEMPT_PREFIXES = (
    _APP_DIR / 'hasn',
    _APP_DIR / 'hasn_core',
)

# 已收编接缝的禁止 import 模式 → 应改用的 façade 入口（错误信息里给修法）。
_BANNED: tuple[tuple[re.Pattern[str], str], ...] = (
    # —— P1 身份接缝 ——
    (
        re.compile(r'from\s+backend\.app\.hasn\.crud\.crud_hasn_(humans|agents)\s+import'),
        'from backend.app.hasn_core import hasn_humans_dao / hasn_agents_dao',
    ),
    (
        re.compile(r'from\s+backend\.app\.hasn\.model\.hasn_(humans|agents)\s+import'),
        'from backend.app.hasn_core import HasnHumans / HasnAgents',
    ),
    (
        re.compile(r'from\s+backend\.app\.hasn\.model\s+import\s+.*\b(HasnHumans|HasnAgents)\b'),
        'from backend.app.hasn_core import HasnHumans / HasnAgents',
    ),
    # —— P2 应用平台服务接缝 ——
    (
        re.compile(
            r'from\s+backend\.app\.hasn\.service\.'
            r'(ai_native_runtime_gateway|ai_native_app_registry|workbench_domain_service'
            r'|instance_resolver|agent_capability_guard|ai_native_knowledge_manifest)\s+import'
        ),
        'from backend.app.hasn_core.app_platform import …',
    ),
    (
        re.compile(r'from\s+backend\.app\.hasn\.service\.app_catalog_registry\s+import\s+app_catalog_registry\b'),
        'from backend.app.hasn_core.app_platform import app_catalog_registry',
    ),
    (
        re.compile(r'from\s+backend\.app\.hasn\.service\.app_catalog_service\s+import'),
        'from backend.app.hasn_core.app_platform import grant_entitlement',
    ),
    (
        re.compile(r'from\s+backend\.app\.hasn\.service\s+import\s+app_catalog_service\b'),
        'from backend.app.hasn_core.app_platform import app_catalog_service',
    ),
    (
        re.compile(
            r'from\s+backend\.app\.hasn\.schema\.'
            r'(ai_native_runtime|hasn_app_entitlement|hasn_builtin_task_catalog)\s+import'
        ),
        'from backend.app.hasn_core.app_platform import …',
    ),
)


def _is_exempt(path: Path) -> bool:
    return any(_is_relative_to(path, p) for p in _EXEMPT_PREFIXES)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def test_no_reachback_into_collapsed_facade_seams() -> None:
    violations: list[str] = []
    for py in _APP_DIR.rglob('*.py'):
        if _is_exempt(py):
            continue
        text = py.read_text(encoding='utf-8')
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith('from '):
                continue
            for pattern, fix in _BANNED:
                if pattern.search(stripped):
                    rel = py.relative_to(_REPO_ROOT)
                    violations.append(f'{rel}:{lineno}: {stripped}\n        → 改用 {fix}')
    assert not violations, (
        '检测到对已收编 façade 接缝的直接反向 import（应改走 hasn_core / hasn_core.app_platform）：\n'
        + '\n'.join(violations)
    )
