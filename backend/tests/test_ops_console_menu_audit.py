"""运营管理面批次 0：菜单审计映射函数与 CI 守卫的单元测试。

**不连数据库**：菜单行直接以 `MenuRow` 数据结构传入被测纯函数，前端页面用 `tmp_path` 造真实文件。

这套断言重点锁两件事：

1. **映射规则与前端一致**——`component` 只拼一次 `.vue`，且 pageMap 有 `views/` 和 `plugins/`
   两个根。少扫 `plugins/` 会把参数配置、通知公告、代码生成三个在用页面误判成 404；
2. **守卫不会静默放行**——路径写错要炸、空转要算失败、有坏行必须红，并且反过来也要能绿
   （否则「永远红」和「永远绿」一样没有信息量）。
"""

from __future__ import annotations

import re

from pathlib import Path

import pytest

from backend.scripts.ops_console._menu_paths import (
    CATEGORY_APP_SURFACE,
    CATEGORY_NO_PAGE,
    CATEGORY_TABLE_CRUD,
    MenuRow,
    PageIndexError,
    audit_csv_records,
    audit_menus,
    build_page_key_index,
    check_menu_pages,
    classify_row,
    default_frontend_src,
    extract_menu_sql_components,
    loose_page_keys,
    normalize_view_path,
    resolve_component,
    strict_page_key,
    table_crud_target,
)

OPS_CONSOLE_DIR = Path(__file__).resolve().parents[1] / 'scripts' / 'ops_console'


def _menu(
    menu_id: int,
    component: str | None,
    *,
    parent_id: int | None = None,
    menu_type: int = 1,
    title: str = '测试菜单',
    status: int = 1,
) -> MenuRow:
    """造一行 `sys_menu`。默认是普通菜单（type=1、正常状态）。"""
    return MenuRow(
        id=menu_id,
        parent_id=parent_id,
        title=title,
        name=f'Menu{menu_id}',
        path=f'/p{menu_id}',
        component=component,
        type=menu_type,
        status=status,
    )


@pytest.fixture
def frontend_src(tmp_path: Path) -> Path:
    """造一个最小前端 src：`views/` 与 `plugins/` 双根各放几个真实 `.vue`。"""
    src = tmp_path / 'src'
    files = [
        src / 'views' / 'hasn' / 'hasn_agents' / 'index.vue',
        src / 'views' / 'hasn' / 'hasn_follows' / 'index.vue',
        src / 'views' / 'hasn_growth' / 'customer' / 'index.vue',
        src / 'views' / '_core' / 'fallback' / 'iframe.vue',
        src / 'views' / 'dashboard' / 'analytics' / 'index.vue',
        src / 'plugins' / 'config' / 'views' / 'index.vue',
    ]
    for file in files:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('<template><div /></template>\n', encoding='utf-8')
    return src


# ---------------------------------------------------------------------------
# 映射规则：normalize_view_path / strict_page_key / loose_page_keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ['raw', 'expected'],
    [
        # 前端 normalizeViewPath 的三步：去相对前缀、补首斜杠、去掉开头的 /views
        ['/hasn/hasn_agents/index', '/hasn/hasn_agents/index'],
        ['hasn/hasn_agents/index', '/hasn/hasn_agents/index'],
        ['../views/hasn/hasn_agents/index.vue', '/hasn/hasn_agents/index.vue'],
        ['./views/hasn/hasn_agents/index.vue', '/hasn/hasn_agents/index.vue'],
        ['../plugins/config/views/index.vue', '/plugins/config/views/index.vue'],
        ['/views/dashboard/analytics/index', '/dashboard/analytics/index'],
        ['plugins/config/views/index', '/plugins/config/views/index'],
    ],
)
def test_normalize_view_path_ports_frontend_rule(raw: str, expected: str) -> None:
    assert normalize_view_path(raw) == expected


@pytest.mark.parametrize(
    ['component', 'expected'],
    [
        # 只拼一次 .vue，已带 .vue 的不重复拼（实测 type=3/4 的行就是 /_core/fallback/iframe.vue）
        ['/hasn/hasn_agents/index', '/hasn/hasn_agents/index.vue'],
        ['/_core/fallback/iframe.vue', '/_core/fallback/iframe.vue'],
        ['/plugins/config/views/index', '/plugins/config/views/index.vue'],
    ],
)
def test_strict_page_key_appends_vue_once(component: str, expected: str) -> None:
    assert strict_page_key(component) == expected


@pytest.mark.parametrize('component', [None, '', '   ', 'BasicLayout', 'IFrameView'])
def test_strict_page_key_skips_empty_and_layout(component: str | None) -> None:
    """空组件与 layout 组件不查 pageMap，不能被当成「无页面」。"""
    assert strict_page_key(component) is None


def test_loose_page_keys_only_for_non_index_paths() -> None:
    assert loose_page_keys('/hasn/hasn_agents') == ('/hasn/hasn_agents/index.vue',)
    # 已经是 /index 或已带 .vue 的没有宽松候选，避免造出 /a/index/index.vue 这种幻觉路径
    assert loose_page_keys('/hasn/hasn_agents/index') == ()
    assert loose_page_keys('/_core/fallback/iframe.vue') == ()
    assert loose_page_keys(None) == ()


# ---------------------------------------------------------------------------
# 页面索引：双根扫描 + 路径写错必须炸（守卫防自我失效的第一道锁）
# ---------------------------------------------------------------------------


def test_build_page_key_index_covers_views_and_plugins(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    # views 根：key 去掉 /views 前缀
    assert '/hasn/hasn_agents/index.vue' in index
    # plugins 根：key 保留 /plugins 前缀（前端 normalizeViewPath 只剥 /views）
    assert '/plugins/config/views/index.vue' in index
    assert index['/plugins/config/views/index.vue'].name == 'index.vue'
    assert len(index) == 6


def test_build_page_key_index_raises_when_src_missing(tmp_path: Path) -> None:
    """路径写错必须抛异常，而不是返回空索引把所有菜单判成 404（那会导致误删全部菜单）。"""
    with pytest.raises(PageIndexError, match='前端 src 根目录不存在'):
        build_page_key_index(tmp_path / '压根不存在')


def test_build_page_key_index_raises_when_plugins_root_missing(tmp_path: Path) -> None:
    """只有 views/ 没有 plugins/ 时也要炸——少扫一个根等于把在用插件页误判成 404。"""
    src = tmp_path / 'src'
    only_views = src / 'views' / 'a' / 'index.vue'
    only_views.parent.mkdir(parents=True)
    only_views.write_text('<template />', encoding='utf-8')

    with pytest.raises(PageIndexError, match='plugins'):
        build_page_key_index(src)


def test_build_page_key_index_raises_when_no_vue_found(tmp_path: Path) -> None:
    src = tmp_path / 'src'
    (src / 'views').mkdir(parents=True)
    (src / 'plugins').mkdir(parents=True)

    with pytest.raises(PageIndexError, match='一个 .vue 都没扫到'):
        build_page_key_index(src)


# ---------------------------------------------------------------------------
# 组件解析：views 命中 / plugins 命中 / 真缺失 / 宽松命中不算命中
# ---------------------------------------------------------------------------


def test_resolve_component_hits_views(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    resolution = resolve_component('/hasn/hasn_agents/index', index)
    assert resolution.status == 'ok'
    assert resolution.file is not None
    assert resolution.file.parts[-3:] == ('hasn', 'hasn_agents', 'index.vue')


def test_resolve_component_hits_plugins(frontend_src: Path) -> None:
    """实测在用页面：/plugins/config/views/index = 参数配置。只扫 views 会把它误判成 404。"""
    index = build_page_key_index(frontend_src)
    resolution = resolve_component('/plugins/config/views/index', index)
    assert resolution.status == 'ok'
    assert resolution.file is not None
    assert resolution.file.parts[-4:] == ('plugins', 'config', 'views', 'index.vue')


def test_resolve_component_missing(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    resolution = resolve_component('/hasn/hasn_不存在的表/index', index)
    assert resolution.status == 'missing'
    assert resolution.page_key == '/hasn/hasn_不存在的表/index.vue'
    assert resolution.file is None


def test_resolve_component_index_fallback_is_loose_not_ok(frontend_src: Path) -> None:
    """`/a/b` 只有 `a/b/index.vue` 时前端**打不开**（convertRoutes 只拼一次 .vue）。

    这条断言防的是「为了少报 404 把回落也算命中」——那样会把真 404 留在生产里。
    """
    index = build_page_key_index(frontend_src)
    resolution = resolve_component('/hasn/hasn_agents', index)
    assert resolution.status == 'loose'
    assert resolution.page_key == '/hasn/hasn_agents.vue'
    assert resolution.loose_key == '/hasn/hasn_agents/index.vue'


# ---------------------------------------------------------------------------
# 分类：A / B / C
# ---------------------------------------------------------------------------


def test_classify_missing_page_as_category_a(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    entry = classify_row(_menu(1, '/hasn/hasn_没有的页面/index'), index)
    assert entry.categories == (CATEGORY_NO_PAGE,)


def test_classify_app_surface_as_category_b(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    entry = classify_row(_menu(2, '/hasn_growth/customer/index'), index)
    # 页面存在，所以不是 A；但属于 AI-Native 应用面，是 B
    assert entry.resolution.status == 'ok'
    assert entry.categories == (CATEGORY_APP_SURFACE,)


def test_classify_relation_table_crud_as_category_c(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    entry = classify_row(_menu(3, '/hasn/hasn_follows/index'), index)
    assert entry.categories == (CATEGORY_TABLE_CRUD,)


def test_classify_platform_page_is_kept(frontend_src: Path) -> None:
    """平台运营页（页面在、不是应用面、不是关系表）必须一类都不进，否则清单会误删在用功能。"""
    index = build_page_key_index(frontend_src)
    entry = classify_row(_menu(4, '/hasn/hasn_agents/index'), index)
    assert entry.categories == ()


def test_classify_button_row_is_not_no_page(frontend_src: Path) -> None:
    """按钮权限行 component 为空，必须单独归类，不能混进「无页面」。"""
    index = build_page_key_index(frontend_src)
    entry = classify_row(_menu(5, None, menu_type=2, title='新增'), index)
    assert entry.categories == ()
    assert entry.resolution.status == 'skipped'
    assert '按钮权限行' in entry.notes[0]


def test_classify_layout_directory_is_not_no_page(frontend_src: Path) -> None:
    """目录行的 component 是 BasicLayout，走 layoutMap，不能判成 404。"""
    index = build_page_key_index(frontend_src)
    entry = classify_row(_menu(6, 'BasicLayout', menu_type=0), index)
    assert entry.categories == ()
    assert entry.resolution.status == 'skipped'


@pytest.mark.parametrize(
    ['component', 'expected'],
    [
        ['/hasn/hasn_follows/index', ('hasn', 'hasn_follows')],
        ['/hasn/hasn_follows/index.vue', ('hasn', 'hasn_follows')],
        ['/hasn/hasn_follows/detail', None],
        ['/plugins/config/views/index', None],
    ],
)
def test_table_crud_target(component: str, expected: tuple[str, str] | None) -> None:
    assert table_crud_target(component) == expected


# ---------------------------------------------------------------------------
# 审计汇总：孤儿按钮、空目录、剩余数、反向清单
# ---------------------------------------------------------------------------


def test_audit_counts_orphan_buttons_and_remaining(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    rows = [
        _menu(1, 'BasicLayout', menu_type=0, title='Hasn'),
        _menu(10, '/hasn/hasn_agents/index', parent_id=1, title='分身'),
        _menu(11, None, parent_id=10, menu_type=2, title='新增'),
        _menu(20, '/hasn/hasn_没有的页面/index', parent_id=1, title='废页'),
        _menu(21, None, parent_id=20, menu_type=2, title='新增'),
        _menu(22, None, parent_id=20, menu_type=2, title='删除'),
    ]
    result = audit_menus(rows, index)

    assert result.doomed_ids == {20}
    # 两个按钮权限行随父菜单成为孤儿，必须计入删除影响
    assert result.orphan_ids == {21, 22}
    assert result.total == 6
    assert result.remaining == 3
    # 目录 1 下还有存活的子菜单 10，不算空目录
    assert result.empty_parent_ids == frozenset()


def test_audit_flags_directory_that_becomes_empty(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    rows = [
        _menu(1, 'BasicLayout', menu_type=0, title='只剩废页的目录'),
        _menu(10, '/hasn/hasn_没有的页面/index', parent_id=1),
    ]
    result = audit_menus(rows, index)
    assert result.empty_parent_ids == {1}


def test_audit_lists_pages_without_menu(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    rows = [_menu(1, '/hasn/hasn_agents/index')]
    result = audit_menus(rows, index)

    # 被菜单指向的那一个不出现在反向清单里，其余 5 个都在
    assert len(result.unreferenced_pages) == 5
    assert all('hasn_agents' not in str(file) for file in result.unreferenced_pages)
    assert {file.name for file in result.unreferenced_pages} == {'index.vue', 'iframe.vue'}


def test_audit_csv_records_expose_action_column(frontend_src: Path) -> None:
    index = build_page_key_index(frontend_src)
    rows = [
        _menu(10, '/hasn/hasn_agents/index'),
        _menu(20, '/hasn/hasn_没有的页面/index'),
        _menu(21, None, parent_id=20, menu_type=2),
    ]
    records = {record['id']: record for record in audit_csv_records(audit_menus(rows, index))}

    assert records['10']['action'] == '保留'
    assert records['20']['action'].startswith('待删')
    assert records['21']['action'].startswith('连带删除')
    assert records['20']['categories'] == CATEGORY_NO_PAGE


# ---------------------------------------------------------------------------
# 守卫：必须能红（反例）、必须能绿（正例）、不能空转
# ---------------------------------------------------------------------------


def test_guard_passes_when_every_menu_has_a_page(frontend_src: Path) -> None:
    """正例。没有这条，「守卫永远红」和「守卫有效」就分不出来。"""
    index = build_page_key_index(frontend_src)
    rows = [
        _menu(1, 'BasicLayout', menu_type=0),
        _menu(10, '/hasn/hasn_agents/index', parent_id=1),
        _menu(11, '/plugins/config/views/index', parent_id=1),
        _menu(12, '/_core/fallback/iframe.vue', parent_id=1, menu_type=4),
        _menu(13, None, parent_id=10, menu_type=2),
    ]
    report = check_menu_pages(rows, index)

    assert report.failures == ()
    assert report.ok is True
    # 3 行真校验（BasicLayout 与按钮行跳过）——checked 必须 > 0，否则是空转
    assert report.checked == 3
    assert report.skipped == 2


def test_guard_fails_on_menu_pointing_to_missing_page(frontend_src: Path) -> None:
    """反例：一条指向不存在页面的假菜单，守卫必须红并点名到行。"""
    index = build_page_key_index(frontend_src)
    rows = [
        _menu(10, '/hasn/hasn_agents/index'),
        _menu(99, '/hasn/根本没有这个页面/index', title='幽灵菜单'),
    ]
    report = check_menu_pages(rows, index)

    assert report.ok is False
    assert [failure.row.id for failure in report.failures] == [99]
    assert report.failures[0].page_key == '/hasn/根本没有这个页面/index.vue'
    assert report.failures[0].suggestion is None


def test_guard_fails_on_loose_only_hit_with_suggestion(frontend_src: Path) -> None:
    """只有 `index.vue` 兜底的写法在前端仍是 404，守卫必须红，但给出「改写法」而非「删」的建议。"""
    index = build_page_key_index(frontend_src)
    report = check_menu_pages([_menu(50, '/hasn/hasn_agents')], index)

    assert report.ok is False
    assert report.failures[0].suggestion == '把 component 改成 /hasn/hasn_agents/index'


def test_guard_is_vacuous_without_rows(frontend_src: Path) -> None:
    """一行都没校验 = 守卫失效，必须按失败处理，不能报「通过」。"""
    index = build_page_key_index(frontend_src)
    report = check_menu_pages([], index)

    assert report.checked == 0
    assert report.vacuous is True
    assert report.ok is False


def test_guard_is_vacuous_when_all_rows_are_buttons(frontend_src: Path) -> None:
    """只剩按钮权限行时同样是空转——这正是「映射函数全返回 None」会呈现的样子。"""
    index = build_page_key_index(frontend_src)
    report = check_menu_pages([_menu(1, None, menu_type=2), _menu(2, None, menu_type=2)], index)

    assert report.checked == 0
    assert report.vacuous is True
    assert report.ok is False


def test_guard_fails_when_page_index_is_empty_but_rows_exist() -> None:
    """页面索引为空（例如目录指错）时不能「全部找不到 → 报一堆假 404」而不自省。"""
    report = check_menu_pages([_menu(1, '/hasn/hasn_agents/index')], {})

    assert report.page_index_size == 0
    assert report.vacuous is True
    assert report.ok is False


def test_guard_does_not_pass_everything(frontend_src: Path) -> None:
    """反向自检：随手编一个组件路径必须被判失败，证明守卫不是「见谁都放行」。"""
    index = build_page_key_index(frontend_src)
    report = check_menu_pages([_menu(1, '/随便写的/一个路径/index')], index)

    assert report.checked == 1
    assert len(report.failures) == 1


# ---------------------------------------------------------------------------
# 脚本自身的静态守卫：只读
# ---------------------------------------------------------------------------

_DML_PATTERNS: tuple[tuple[str, str], ...] = (
    ('SQLAlchemy 写语句', r'\bsa\.(?:delete|update|insert)\s*\('),
    ('原生 DML', r'\b(?:delete\s+from|insert\s+into|truncate\s+table)\b'),
    ('原生 UPDATE', r'\bupdate\s+\w+\s+set\b'),
    ('事务提交', r'\.commit\s*\('),
    ('DAO 写方法', r'\b(?:delete_model|update_model|create_model)\b'),
)


def _find_dml(text: str) -> list[str]:
    """在源码文本里找写操作痕迹，返回命中的类别名。"""
    return [label for label, pattern in _DML_PATTERNS if re.search(pattern, text, flags=re.IGNORECASE)]


def test_dml_detector_itself_works() -> None:
    """守卫的守卫：检测器本身必须能认出写操作，否则下面那条断言会静默变成空转。"""
    assert _find_dml('await db.execute(sa.delete(Menu))') == ['SQLAlchemy 写语句']
    assert _find_dml("await db.execute(text('DELETE FROM sys_menu'))") == ['原生 DML']
    assert _find_dml('await db.commit()') == ['事务提交']
    assert _find_dml('rows = await db.execute(sa.select(Menu))') == []


@pytest.mark.parametrize('script_name', ['audit_menus.py', 'check_menu_pages.py', '_menu_paths.py'])
def test_ops_console_scripts_are_read_only(script_name: str) -> None:
    """审计与守卫脚本一律只读：出现任何写操作即视为越权（删除属于 T0.2，由人工执行）。"""
    source = (OPS_CONSOLE_DIR / script_name).read_text(encoding='utf-8')
    assert _find_dml(source) == []


def test_ops_console_scripts_have_no_execute_switch() -> None:
    """审计脚本不得提供 `--execute` 之类的开关——这是施工清单 T0.1「先看清单，再动手」的硬约束。"""
    source = (OPS_CONSOLE_DIR / 'audit_menus.py').read_text(encoding='utf-8')
    assert "'--execute'" not in source
    assert "'--apply'" not in source
    assert "'--delete'" not in source


# ---------------------------------------------------------------------------
# 菜单 SQL 抽取 + 默认前端路径
# ---------------------------------------------------------------------------


def test_extract_menu_sql_components(tmp_path: Path) -> None:
    sql_dir = tmp_path / 'sql'
    (sql_dir / 'generated').mkdir(parents=True)
    (sql_dir / 'generated' / 'demo_menu.sql').write_text(
        "INSERT INTO sys_menu (path, component) VALUES ('/hasn/demo', '/hasn/demo/index');\n"
        "UPDATE sys_menu SET component = '/hasn/demo/index';\n",
        encoding='utf-8',
    )
    # 文件名不含 menu 的不参与抽取
    (sql_dir / 'generated' / 'demo_table.sql').write_text(
        "INSERT INTO whatever VALUES ('/other/thing/index');\n", encoding='utf-8'
    )

    found = extract_menu_sql_components(sql_dir)
    assert set(found) == {'/hasn/demo/index'}
    assert found['/hasn/demo/index'][0].name == 'demo_menu.sql'


def test_extract_menu_sql_components_ignores_path_column(tmp_path: Path) -> None:
    """`path` 列长得像组件（'/fba/document'），但不以 /index 或 .vue 结尾，不能被抽进来。"""
    sql_dir = tmp_path / 'sql'
    sql_dir.mkdir()
    (sql_dir / 'x_menu.sql').write_text("VALUES ('/fba/document', 'BasicLayout');\n", encoding='utf-8')
    assert extract_menu_sql_components(sql_dir) == {}


@pytest.mark.skipif(default_frontend_src() is None, reason='本机没有并列检出 hasn-cloud-frontend 仓')
def test_default_frontend_src_points_at_a_real_dual_root_src() -> None:
    """自动推导的默认路径必须真的是 src 根（两个 glob 根都在），否则守卫会大面积误报。"""
    src = default_frontend_src()
    assert src is not None
    assert (src / 'views').is_dir()
    assert (src / 'plugins').is_dir()
    assert src.name == 'src'
