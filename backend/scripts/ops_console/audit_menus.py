"""运营管理面批次 0 · T0.1：`sys_menu` 与前端页面的 dry-run 审计。

**本脚本只读**：只跑 SELECT，不提供 `--execute` 之类的开关，也不写任何业务表。删除动作属于 T0.2，
由人工过完清单后另行执行（`backend/tests/test_ops_console_menu_audit.py` 里有一条静态守卫，
断言本文件与 `check_menu_pages.py` 里不出现任何 DML 与 `commit`）。

输出：

- **A 无页面**：`component` 指向的 `.vue` 不存在；
- **B 应用面**：路径命中 AI-Native 应用前缀 / 脚手架演示目录（前缀常量在 `_menu_paths.py` 顶部）；
- **C 按表 CRUD**：`component` 形如 `/<模块>/<表名>/index` 且表属于关系/社区类；
- 反向清单：有 `.vue` 页面却没有任何菜单指向它；
- 汇总：各类数量、总数、删除后剩余菜单数、按钮孤儿数；
- 对照：`backend/sql/**/*menu*.sql` 里抽到的组件路径 vs 库里实际的行（施工清单 §1 的 313 条来自
  文件推算，与库里的行不是一回事）；
- CSV：全量菜单行 + `action` 列（待删 / 连带删除 / 待人工确认 / 保留），供人工整表复核。

用法：

    uv run python -m backend.scripts.ops_console.audit_menus
    uv run python -m backend.scripts.ops_console.audit_menus --frontend-src /path/to/web-antdv-next/src
    uv run python -m backend.scripts.ops_console.audit_menus --csv /tmp/待删菜单清单.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys

from pathlib import Path

import sqlalchemy as sa

from sqlalchemy.exc import SQLAlchemyError

from backend.app.admin.model.menu import Menu
from backend.core.path_conf import BASE_PATH
from backend.database.db import async_db_session
from backend.scripts.ops_console._menu_paths import (
    CATEGORY_APP_SURFACE,
    CATEGORY_NO_PAGE,
    CATEGORY_TABLE_CRUD,
    CATEGORY_TITLES,
    CSV_FIELDS,
    MENU_TYPE_LABELS,
    AuditResult,
    ClassifiedMenu,
    MenuRow,
    PageIndexError,
    audit_csv_records,
    audit_menus,
    build_page_key_index,
    default_frontend_src,
    extract_menu_sql_components,
    menu_type_counts,
    unclassified_modules,
)

async def load_menu_rows() -> list[MenuRow]:
    """从 `sys_menu` 读取全部行（**只 SELECT**）。连不上库时显式报错，绝不返回空清单冒充成功。"""
    try:
        async with async_db_session() as db:
            result = await db.execute(
                sa.select(
                    Menu.id,
                    Menu.parent_id,
                    Menu.title,
                    Menu.name,
                    Menu.path,
                    Menu.component,
                    Menu.type,
                    Menu.status,
                ).order_by(Menu.id)
            )
            rows = [
                MenuRow(
                    id=record.id,
                    parent_id=record.parent_id,
                    title=record.title,
                    name=record.name,
                    path=record.path,
                    component=record.component,
                    type=record.type,
                    status=record.status,
                )
                for record in result.all()
            ]
    except SQLAlchemyError:
        print('[错误] 读取 sys_menu 失败：数据库连接或查询出错，审计中止（不会给出任何结论）。', file=sys.stderr)
        raise

    if not rows:
        raise RuntimeError('sys_menu 一行都没有读到——库连错了或表是空的，审计中止，不要按空结果做判断')
    return rows


def _print_section(title: str) -> None:
    print(f'\n{"=" * 100}\n{title}\n{"=" * 100}')


def _print_entries(entries: tuple[ClassifiedMenu, ...]) -> None:
    """打印一张分类表。列齐 id/parent_id/title/path/component/type/status，便于人工复核。"""
    if not entries:
        print('（空）')
        return

    header = f'{"id":>6} {"parent":>7} {"type":<6} {"状态":<4} {"标题":<22} {"path":<44} {"component"}'
    print(header)
    print('-' * 100)
    for entry in entries:
        row = entry.row
        type_label = f'{row.type}{MENU_TYPE_LABELS.get(row.type, "?")}'
        status_label = '正常' if row.status == 1 else '停用'
        parent = '' if row.parent_id is None else str(row.parent_id)
        print(
            f'{row.id:>6} {parent:>7} {type_label:<6} {status_label:<4} '
            f'{row.title:<22} {row.path or "":<44} {row.component or ""}'
        )
        for note in entry.notes:
            print(f'{"":>6} {"":>7} └─ {note}')


def _print_summary(result: AuditResult, rows: list[MenuRow]) -> None:
    _print_section('汇总')
    type_counts = menu_type_counts(rows)
    type_text = '、'.join(
        f'{menu_type}{MENU_TYPE_LABELS.get(menu_type, "?")} {count}' for menu_type, count in type_counts.items()
    )
    print(f'sys_menu 总行数：{result.total}（{type_text}）')
    print(f'前端页面索引（views + plugins 双根）：{result.page_index_size} 个 .vue')
    print()

    a_entries = result.by_category(CATEGORY_NO_PAGE)
    b_entries = result.by_category(CATEGORY_APP_SURFACE)
    c_entries = result.by_category(CATEGORY_TABLE_CRUD)
    loose = tuple(entry for entry in result.entries if entry.resolution.status == 'loose')
    ok_entries = tuple(entry for entry in result.entries if entry.resolution.status == 'ok')

    print(f'A 无页面：{len(a_entries)}')
    print(f'B 应用面：{len(b_entries)}')
    print(f'C 按表 CRUD：{len(c_entries)}')
    print(f'A∪B∪C 去重后直接命中：{len(result.doomed_ids)}')
    print(f'连带孤儿（父被删的后代，含按钮权限行）：{len(result.orphan_ids)}')
    print(f'删除后剩余菜单数：{result.remaining}（{result.total} − {len(result.removed_ids)}）')
    print(f'删除后会变成空目录的目录行：{len(result.empty_parent_ids)}')
    print()
    print(f'组件解析成功（前端真能打开）：{len(ok_entries)}')
    print(f'仅宽松命中（写法疑似写错，前端仍 404，**不要直接删**）：{len(loose)}')
    print(f'按钮权限行（component 恒空，不参与页面比对）：{len(result.button_rows)}')
    print(f'有页面但无菜单指向：{len(result.unreferenced_pages)}')


def _print_sql_file_comparison(rows: list[MenuRow], sql_dir: Path) -> None:
    """对照「菜单 SQL 文件里的组件路径」与「库里实际的行」，帮人发现文件有、库里没有的部分。"""
    _print_section('对照：菜单 SQL 文件 vs 数据库')
    try:
        sql_components = extract_menu_sql_components(sql_dir)
    except PageIndexError as error:
        print(f'[跳过] {error}')
        return

    db_components = {row.component.strip() for row in rows if row.component and row.component.strip()}
    file_only = sorted(set(sql_components) - db_components)
    db_only = sorted(component for component in db_components if component not in sql_components)

    print(f'SQL 文件（{sql_dir}）里抽到的组件路径：{len(sql_components)}（启发式正则，非 SQL 解析）')
    print(f'数据库 sys_menu 里的去重组件路径：{len(db_components)}')
    print(f'仅出现在 SQL 文件、库里没有：{len(file_only)}（重跑种子会把它们长回来，T0.2 需同批删文件）')
    print(f'仅出现在库里、SQL 文件没有：{len(db_only)}（手工建的菜单，删它们不影响生成脚本）')
    for component in file_only:
        files = sql_components[component]
        names = '、'.join(sorted({file.name for file in files}))
        print(f'  文件独有 {component}  ←  {names}')


def _print_orphans(result: AuditResult) -> None:
    """按钮权限行单列一类，并按父菜单给出「删父即孤儿」的清单。

    施工清单 T0.2 要求「先删子节点再删父目录」（`parent_id` 有外键语义），所以这张表就是删除顺序表：
    每一组先删右边的按钮行，再删左边的父菜单。
    """
    _print_section('按钮权限行与孤儿提示')
    print(f'按钮权限行（type=2）共 {len(result.button_rows)} 行，component 恒为空，不参与页面比对。')
    print(f'其中 {len(result.orphan_ids)} 行会因父节点被删而成为孤儿，必须随父一起清理。')
    print('T0.2 删除顺序：先删下表右侧的子行，再删左侧的父行（parent_id 有外键语义）。\n')

    by_row = {entry.row.id: entry.row for entry in result.entries}
    grouped: dict[int, list[int]] = {}
    for orphan_id in sorted(result.orphan_ids):
        parent_id = by_row[orphan_id].parent_id
        if parent_id is not None:
            grouped.setdefault(parent_id, []).append(orphan_id)

    if not grouped:
        print('（无孤儿）')
        return
    for parent_id in sorted(grouped):
        parent = by_row.get(parent_id)
        title = parent.title if parent is not None else '（父行不在本次结果里）'
        children = grouped[parent_id]
        print(f'父 {parent_id:>6} {title:<28} → 连带 {len(children):>3} 行：{children}')


def _print_unclassified(result: AuditResult) -> None:
    _print_section('未分类模块（解析成功且未命中 A/B/C，供人工判断是否漏了应用前缀）')
    counter = unclassified_modules(result)
    if not counter:
        print('（空）')
        return
    for module, count in counter.items():
        print(f'{count:>4}  /{module}/')


def _write_csv(result: AuditResult, csv_path: Path) -> None:
    records = audit_csv_records(result)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：Excel 直接双击打开中文不乱码，人工复核用得上
    with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        writer.writerows(records)
    print(f'\nCSV 已写出：{csv_path}（{len(records)} 行，含全量菜单与 action 列）')
    print('施工清单 T0.1 要求这份清单人工过一遍再执行删除；产物进 PR 描述，不进代码库。')


def _resolve_frontend_src(argument: str | None) -> Path:
    if argument is not None:
        return Path(argument).expanduser()
    discovered = default_frontend_src()
    if discovered is None:
        raise SystemExit(
            '[错误] 没有自动找到前端 src 目录（父项目下的 hasn-cloud-frontend/apps/web-antdv-next/src），'
            '请用 --frontend-src 显式指定。不猜路径：猜错会把在用页面全判成 404。'
        )
    return discovered


async def _run(frontend_src: Path, csv_path: Path, sql_dir: Path) -> None:
    page_index = build_page_key_index(frontend_src)
    rows = await load_menu_rows()
    result = audit_menus(rows, page_index)

    print(f'前端 src：{frontend_src}')
    print('页面索引扫描根：views/ 与 plugins/（与前端 access.ts 的两个 import.meta.glob 对齐）')

    for category in (CATEGORY_NO_PAGE, CATEGORY_APP_SURFACE, CATEGORY_TABLE_CRUD):
        entries = result.by_category(category)
        _print_section(f'{CATEGORY_TITLES[category]} — {len(entries)} 行')
        _print_entries(entries)

    loose_entries = tuple(entry for entry in result.entries if entry.resolution.status == 'loose')
    _print_section(f'疑似写法写错（严格规则 404，但同目录有 index.vue）— {len(loose_entries)} 行')
    print('这类行**不进删除清单**：前端不会回落到 index.vue，它们现在确实 404，但改 component 就能救。')
    _print_entries(loose_entries)

    _print_section(f'反向清单：有页面但没有菜单指向 — {len(result.unreferenced_pages)} 个 .vue')
    print('可能是漏配菜单，也可能是废弃页面；本脚本不区分，交人工判断。')
    for file in result.unreferenced_pages:
        print(f'  {file.relative_to(frontend_src)}')

    _print_orphans(result)
    _print_unclassified(result)
    _print_sql_file_comparison(rows, sql_dir)
    _print_summary(result, rows)
    _write_csv(result, csv_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='sys_menu 与前端页面的 dry-run 审计（只读，不执行任何删除）',
    )
    parser.add_argument(
        '--frontend-src',
        '--views-dir',
        dest='frontend_src',
        default=None,
        help=(
            '前端 src 根目录（apps/web-antdv-next/src）。注意是 **src** 不是 views：'
            'pageMap 同时 glob views/ 和 plugins/，只给 views 会把 plugins 下的在用页面误判成 404。'
            '缺省时从本文件向上自动寻找父项目里的前端仓。'
        ),
    )
    parser.add_argument(
        '--csv',
        dest='csv_path',
        default='sys_menu_audit.csv',
        help='CSV 输出路径（施工清单里称它「待删菜单清单.csv」），默认写到当前目录',
    )
    parser.add_argument(
        '--sql-dir',
        dest='sql_dir',
        default=str(BASE_PATH / 'sql'),
        help='菜单 SQL 目录，用于和库里的行做量级对照，默认 backend/sql',
    )
    args = parser.parse_args()

    frontend_src = _resolve_frontend_src(args.frontend_src)
    asyncio.run(_run(frontend_src, Path(args.csv_path).expanduser(), Path(args.sql_dir).expanduser()))


if __name__ == '__main__':
    main()
