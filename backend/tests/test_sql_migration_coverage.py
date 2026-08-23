"""SQL 迁移覆盖守卫（部署流程硬规则）。

**规则**：生产迁移 runner（服务器上的 `run_pending_migrations.sh`）只执行

    find "$SQL_ROOT" -path "*/migrations/*.sql"

也就是说——**只有 `backend/sql/<模块>/migrations/*.sql` 会在生产被执行**。
放在 `backend/sql/<模块>/` 下但不在 `migrations/` 子目录里的 SQL 文件，
**从未也不会**被生产执行，而且**不会有任何报错**：它就是静默地什么都不做。

**这条规则失效过一次，代价是素材站整个功能在生产上从未可用（2026-07-12 → 2026-08-23，六周）**：
`hasn_stock/hasn_stock_providers.sql` 含建表 + 三行种子，却放在模块根目录下。结果是——
- 表被启动期 `metadata.create_all` 建了出来（只建表、不种数据），所以「表存在」骗过了排查；
- 种子三行从未执行 → `hasn_stock.hasn_stock_providers` 长期 0 行；
- `cached_source_enum()` 读到空的 enabled 集合 → tool schema 里 `source` enum 渲染成 `[]`；
- `hasn.stock.search` 报「没有支持 image/video 的已启用素材站，请在后台配置」。
同批的 `hasn_stock_providers_menu.sql`（管理端菜单 + RBAC 按钮权限）同样从未执行，
于是管理员在后台**连配置页面都看不到**，无从自救。

⚠️ **生产已关闭启动期 `create_all`**（R1-11，`backend/database/db.py::_should_auto_create_tables`
对 `ENVIRONMENT=='prod'` 恒 False）。这意味着今天新增的表若只写在 `migrations/` 之外，
**在生产上连表都不会存在**——比上面这次更早炸、也更明显，但根因是同一个。

本守卫只挡**新增漂移**：任何含 `CREATE TABLE` / `INSERT` 的 SQL 文件，若不在 `migrations/` 下
且不在基线里 → 失败。届时：
- 需要在生产生效（建表 / 种子 / 改数据）→ 放进 `backend/sql/<模块>/migrations/YYYY-MM-DD-*.sql`，
  写成幂等（`IF NOT EXISTS` / `ON CONFLICT` / `ADD COLUMN IF NOT EXISTS`）；
- 确实不需要在生产执行（本地夹具、一次性排查脚本、参考副本）→ 显式加进基线并写明理由。

基线文件：`backend/sql/.migration-coverage-baseline.txt`（215 条存量欠债，2026-08-23 冻结）。
把某个存量文件迁进 `migrations/` 后，记得从基线删掉对应行（`test_no_stale_baseline` 会提醒）。
"""

from __future__ import annotations

import re

from pathlib import Path

# backend/tests/ → backend/ → backend/sql/
_SQL_ROOT = Path(__file__).resolve().parent.parent / 'sql'
_BASELINE_FILE = _SQL_ROOT / '.migration-coverage-baseline.txt'

# 结构性排除：这些目录整体不参与生产部署，且原因与「忘了放 migrations/」无关。
#   generated/  —— `fba codegen` 的输出物（建表语句的代码生成副本），不是部署源；
#   tables/     —— 早期表定义副本，历史布局，同样不是部署源；
#   postgresql/ —— 本地测试夹具（init_test_data 等），只在开发机手工跑；
#   _archive/   —— 已归档的历史 SQL。
# ⚠️ 排除的是「这些目录」，不是「这些目录里的风险」——它们同样不会在生产执行。
_EXCLUDED_DIRS = frozenset({'generated', 'tables', 'postgresql', '_archive'})

# 判据：会产生 schema 或数据的语句。行首匹配，避免命中注释与字符串里的同名词。
_MUTATING = re.compile(r'^\s*(INSERT|CREATE\s+TABLE)', re.IGNORECASE | re.MULTILINE)


def _load_baseline() -> set[str]:
    """读基线（忽略注释行与空行）。"""
    lines = _BASELINE_FILE.read_text(encoding='utf-8').splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith('#')}


def _is_excluded(rel: Path) -> bool:
    """命中结构性排除目录（路径任意一级）。"""
    return bool(_EXCLUDED_DIRS.intersection(rel.parts))


def _mutating_files_outside_migrations() -> set[str]:
    """扫出「含建表/写数据、却不在 migrations/ 下」的 SQL 文件（相对 backend/sql 的路径）。"""
    found: set[str] = set()
    for path in _SQL_ROOT.rglob('*.sql'):
        rel = path.relative_to(_SQL_ROOT)
        if 'migrations' in rel.parts or _is_excluded(rel):
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if _MUTATING.search(text):
            found.add(rel.as_posix())
    return found


def test_baseline_file_exists_and_is_nonempty() -> None:
    """守卫自身有效性：基线文件在位且非空——否则下面两个断言会静默变成恒真。"""
    assert _BASELINE_FILE.is_file(), f'基线文件缺失：{_BASELINE_FILE}'
    assert len(_load_baseline()) > 0, '基线为空，守卫会退化成恒真'


def test_scanner_actually_finds_files() -> None:
    """守卫自身有效性：扫描器确实能扫到文件——防止 glob/正则改错后静默零向量。

    （父仓治理契约踩过：判定存在、向量缺席，而输出照样是「零违规」。）
    """
    assert len(_mutating_files_outside_migrations()) > 0, '扫描器一个文件都没扫到，判据已失效'


def test_no_new_sql_outside_migrations() -> None:
    """新增的建表/种子 SQL 必须落在 migrations/ 下，否则生产永远不会执行它。"""
    offenders = sorted(_mutating_files_outside_migrations() - _load_baseline())
    assert not offenders, (
        '以下 SQL 含 CREATE TABLE / INSERT 却不在 migrations/ 下，'
        '生产 runner 不会执行它们（静默无效，无任何报错）：\n  '
        + '\n  '.join(offenders)
        + '\n\n需要在生产生效 → 移到 backend/sql/<模块>/migrations/YYYY-MM-DD-*.sql 并写成幂等；'
        '\n确实不需要在生产执行 → 加进 backend/sql/.migration-coverage-baseline.txt 并写明理由。'
    )


def test_no_stale_baseline() -> None:
    """基线不得留下已不存在 / 已迁进 migrations/ 的陈旧条目（防止基线只涨不减）。"""
    stale = sorted(_load_baseline() - _mutating_files_outside_migrations())
    assert not stale, (
        '以下基线条目已不再命中（文件已删除，或已迁进 migrations/），请从基线删除：\n  ' + '\n  '.join(stale)
    )
