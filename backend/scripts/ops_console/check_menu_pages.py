"""运营管理面批次 0 · T0.2 守卫：`sys_menu` 每一行的组件都必须能在前端找到 `.vue`。

**只读**。有任何一行找不到页面就打印清单并以非零码退出，供 CI 使用；后续新增菜单也走它。

校验范围与后端 `backend/app/admin/crud/crud_menu.py::get_sidebar` 的 `type__in: [0, 1, 3, 4]`
一致——能被下发到侧边栏的行才可能 404。按钮权限行（`type=2`）的 `component` 恒为空，天然落在范围外；
`BasicLayout` / `IFrameView` 走 layoutMap 不查 pageMap，也不校验。

**守卫防自我失效**（静默放行的守卫比没有守卫更糟），三道锁：

1. 前端 src 或它的 `views/` / `plugins/` 任一目录不存在、扫不到 `.vue` → `PageIndexError` 直接炸，
   不会安静地把所有菜单判成「无页面」；
2. 一行都没校验到（`checked == 0`）或页面索引为空 → 判为空转，同样非零退出；
3. 映射函数与守卫判定的单元测试在 `backend/tests/test_ops_console_menu_audit.py`，含反例：
   构造一条指向不存在页面的菜单行，断言守卫必红。

用法：

    uv run python -m backend.scripts.ops_console.check_menu_pages
    uv run python -m backend.scripts.ops_console.check_menu_pages --frontend-src /path/to/src

退出码：0 通过；1 存在找不到页面的菜单行；2 守卫自身无法工作（路径错、库读不到、空转）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pathlib import Path

from backend.scripts.ops_console._menu_paths import (
    MENU_TYPE_LABELS,
    GuardReport,
    PageIndexError,
    build_page_key_index,
    check_menu_pages,
    default_frontend_src,
)
from backend.scripts.ops_console.audit_menus import load_menu_rows

EXIT_OK = 0
EXIT_MENU_WITHOUT_PAGE = 1
EXIT_GUARD_BROKEN = 2


def _print_report(report: GuardReport, frontend_src: Path) -> None:
    print(f'前端 src：{frontend_src}（扫描 views/ 与 plugins/ 两个根，与前端 access.ts 对齐）')
    print(f'页面索引：{report.page_index_size} 个 .vue')
    print(f'已校验菜单行：{report.checked}；跳过（按钮/空组件/layout 组件）：{report.skipped}')

    if not report.failures:
        return

    print(f'\n[失败] {len(report.failures)} 行菜单找不到对应页面：')
    print(f'{"id":>6} {"parent":>7} {"type":<6} {"标题":<22} {"component":<48} 期望文件')
    print('-' * 110)
    for failure in report.failures:
        row = failure.row
        type_label = f'{row.type}{MENU_TYPE_LABELS.get(row.type, "?")}'
        parent = '' if row.parent_id is None else str(row.parent_id)
        print(
            f'{row.id:>6} {parent:>7} {type_label:<6} {row.title:<22} '
            f'{row.component or "":<48} {failure.page_key}'
        )
        if failure.suggestion is not None:
            print(f'{"":>6} {"":>7} └─ {failure.suggestion}')
    print('\n处理方式：菜单确实废弃 → 删 sys_menu 行并同批删掉对应的 backend/sql/**/*menu*.sql；')
    print('          只是路径写错 → 按上面的建议改 component，不要删。')


async def _run(frontend_src: Path) -> int:
    try:
        page_index = build_page_key_index(frontend_src)
    except PageIndexError as error:
        print(f'[错误] 页面索引构建失败：{error}', file=sys.stderr)
        return EXIT_GUARD_BROKEN

    try:
        rows = await load_menu_rows()
    except Exception as error:  # noqa: BLE001 - 守卫读不到库必须显式失败，不能当作「没有问题」
        print(f'[错误] 读取 sys_menu 失败，守卫无法给出结论：{error!r}', file=sys.stderr)
        return EXIT_GUARD_BROKEN

    report = check_menu_pages(rows, page_index)
    _print_report(report, frontend_src)

    if report.vacuous:
        print(
            f'[错误] 守卫空转：checked={report.checked}、page_index_size={report.page_index_size}。'
            '这说明守卫本身失效（没有可校验的行或页面索引为空），按失败处理。',
            file=sys.stderr,
        )
        return EXIT_GUARD_BROKEN
    if report.failures:
        return EXIT_MENU_WITHOUT_PAGE

    print('\n[通过] 所有可下发的菜单行都能找到对应页面。')
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(
        description='CI 守卫：sys_menu 的每一行组件都必须能在前端 views/ 或 plugins/ 找到 .vue',
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
    args = parser.parse_args()

    if args.frontend_src is not None:
        frontend_src = Path(args.frontend_src).expanduser()
    else:
        discovered = default_frontend_src()
        if discovered is None:
            print(
                '[错误] 没有自动找到前端 src 目录（父项目下的 hasn-cloud-frontend/apps/web-antdv-next/src），'
                '请用 --frontend-src 显式指定。守卫不猜路径：猜错会把在用页面全判成 404。',
                file=sys.stderr,
            )
            return EXIT_GUARD_BROKEN
        frontend_src = discovered

    return asyncio.run(_run(frontend_src))


if __name__ == '__main__':
    sys.exit(main())
