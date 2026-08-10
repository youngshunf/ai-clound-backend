"""`sys_menu` 组件路径映射、分类与守卫判定的共享纯逻辑。

**本模块刻意不 import 任何后端运行时模块**（`backend.core.conf` / `backend.database.db` …）。
`audit_menus.py` 与 `check_menu_pages.py` 都要连库，import 它们会拉起 Settings 并要求 `.env`；
把纯逻辑隔离在这里，单元测试才能既不碰数据库也不碰配置，直接把菜单行当数据结构传进来。

## 组件路径怎么映射（这是前端规则的等价移植，不是我们自己发明的约定）

权威实现在前端 `packages/utils/src/helpers/generate-routes-backend.ts`：

```ts
function normalizeViewPath(path) {
  const normalizedPath = path.replace(/^(\\.\\/|\\.\\.\\/)+/, '');
  const viewPath = normalizedPath.startsWith('/') ? normalizedPath : `/${normalizedPath}`;
  return viewPath.replace(/^\\/views/, '');
}
// convertRoutes：
const pageKey = normalizePath.endsWith('.vue') ? normalizePath : `${normalizePath}.vue`;
```

即：`component` 归一化后**只拼一次 `.vue`**，命不中 `pageMap` 就直接落到 not-found 页。
`normalize_view_path` / `strict_page_key` 是这两段的逐行移植。

## 为什么输入是「前端 src 根目录」而不是 views 目录

`apps/web-antdv-next/src/router/access.ts` 的 pageMap 同时 glob 两个根：

```ts
...import.meta.glob('../views/**/*.vue'),
...import.meta.glob('../plugins/**/*.vue'),
```

所以 `component` 既可能指向 `views/`，也可能指向 `plugins/`——实测 `/plugins/config/views/index`
（参数配置）、`/plugins/notice/views/index`（通知公告）、`/plugins/code_generator/views/index`
（代码生成）三个在用页面都在 `plugins/` 下。**只扫 `views/` 会把它们误判成 404，删掉即等于删掉三个
在用功能**。故本模块统一以 `src/` 为输入，内部枚举 `PAGE_ROOTS` 里的每个根。
"""

from __future__ import annotations

import re

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# 常量（人工调整入口都集中在这里）
# ---------------------------------------------------------------------------

# 前端 layoutMap 里的组件名（access.ts 的 `layoutMap`），在 pageMap 之前解析，
# 不对应任何 .vue 文件，因此不参与页面比对。
LAYOUT_COMPONENTS: frozenset[str] = frozenset({'BasicLayout', 'IFrameView'})

# 前端 pageMap 的两个 glob 根，相对 `apps/web-antdv-next/src/`。
PAGE_ROOTS: tuple[str, ...] = ('views', 'plugins')

# 按钮权限行：`type=2`。实测本地库 576 行按钮的 `component` 全为 NULL，天然不参与页面比对，
# 但它们会随父菜单一起被删（成为孤儿），审计里单列一类。
BUTTON_MENU_TYPE = 2

# 后端下发侧边栏时的类型白名单，与 `backend/app/admin/crud/crud_menu.py::get_sidebar`
# 的 `type__in: [0, 1, 3, 4]` 一字不差。守卫的校验范围必须与它对齐：能被下发的行才可能 404。
SIDEBAR_MENU_TYPES: frozenset[int] = frozenset({0, 1, 3, 4})

# B 类：AI-Native 应用面前缀（带首尾斜杠，避免 `/hasn_stock` 命中 `/hasn_stockx`）。
# 依据 `01-功能范围与页面清单.md` §3 与施工清单 T0.3 的后端模块 + 前端 views 目录清单。
# ⚠️ 人工调整入口：菜单里还存在若干**文档未点名**的应用域（实测有 hasn_finance、hasn_designsystem、
# hasn_knowledge、hasn_deck、hasn_plan、hasn_publish、huanxing 等）。是否算「应用面」需要人裁决，
# 故不擅自加入；审计输出的「未分类模块」一节会把它们列出来供人工决定。
AI_NATIVE_APP_PREFIXES: tuple[str, ...] = (
    '/creator/',
    '/hasn_copilot/',
    '/hasn_creator/',
    '/hasn_design/',
    '/hasn_growth/',
    '/hasn_imagelab/',
    '/hasn_project/',
    '/hasn_quant/',
    '/hasn_reel/',
    '/hasn_stock/',
    '/hasn_studio/',
    '/lead_automation/',
)

# 同属批次 0 待删、但不是「应用」的脚手架/演示目录（施工清单 T0.3 前端删除清单里点名）。
# 与应用面并入 B 类输出，CSV 的 reason 列会区分两者。
SCAFFOLD_PREFIXES: tuple[str, ...] = (
    '/codegen_test/',
    '/demos/',
)

# C 类：按表铺开的关系/社区 CRUD。依据 §3「按表铺开的关系/社区 CRUD 菜单｜follows/likes/comments/
# collections 等」。
# ⚠️ 刻意**不含** hasn_messages / hasn_conversations / hasn_contacts / hasn_contact_requests /
# hasn_suppressed_messages：它们是批次 1 §2.3「消息与内容安全」的读数据源，场景页尚未建，
# 现在删表级 CRUD 面会让运营短期内彻底看不到这些数据。按「宁可少删，不可误删」留给人工决定。
RELATION_COMMUNITY_TABLES: frozenset[str] = frozenset({
    'hasn_articles',
    'hasn_collection_items',
    'hasn_collections',
    'hasn_comments',
    'hasn_follows',
    'hasn_group_members',
    'hasn_likes',
    'hasn_posts',
    'hasn_topic_follows',
    'hasn_topics',
    'hasn_unread_counts',
})

# C 类的模块级判据：整个模块都属于社区域时不必逐表登记。
RELATION_COMMUNITY_MODULES: frozenset[str] = frozenset({'hasn_community', 'topic'})

# `sys_menu.type` 的中文标签（0 目录 / 1 菜单 / 2 按钮 / 3 内嵌 / 4 外链），打印表格时用。
MENU_TYPE_LABELS: dict[int, str] = {0: '目录', 1: '菜单', 2: '按钮', 3: '内嵌', 4: '外链'}

CATEGORY_NO_PAGE = 'A'
CATEGORY_APP_SURFACE = 'B'
CATEGORY_TABLE_CRUD = 'C'

CATEGORY_TITLES: dict[str, str] = {
    CATEGORY_NO_PAGE: 'A 无页面（component 指向的 .vue 不存在）',
    CATEGORY_APP_SURFACE: 'B 应用面（AI-Native 应用 / 脚手架演示目录）',
    CATEGORY_TABLE_CRUD: 'C 按表 CRUD（关系/社区类表的 /模块/表名/index）',
}

# 父项目里前端 src 根目录相对父项目根的位置，用于推导 --frontend-src 默认值。
FRONTEND_SRC_RELATIVE = Path('hasn-cloud-frontend/apps/web-antdv-next/src')

_RELATIVE_PREFIX_RE = re.compile(r'^(\./|\.\./)+')
_VIEWS_PREFIX_RE = re.compile(r'^/views')
_TABLE_CRUD_RE = re.compile(r'^/(?P<module>[A-Za-z0-9_]+)/(?P<table>[A-Za-z0-9_]+)/index(?:\.vue)?$')

# 从 `backend/sql/**/*menu*.sql` 里抽组件路径的启发式：只认「以 /index 结尾」或「以 .vue 结尾」的
# 单引号字面量，避免把 `path` 列（如 '/fba/document'）一并算进来。这是**启发式，不是 SQL 解析**，
# 抽出的数量只用于和库里的行做量级对照。
_SQL_COMPONENT_RE = re.compile(r"'(/[A-Za-z0-9_\-/]*?(?:/index|\.vue))'")


class PageIndexError(RuntimeError):
    """前端页面索引构建失败：目录不存在，或一个 `.vue` 都没扫到。

    这条异常是**守卫防自我失效**的关键一环：路径写错时必须炸掉，而不是安静地返回空索引
    把所有菜单都判成「无页面」（那会导致误删全部菜单）。
    """


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

ResolutionStatus = Literal['ok', 'loose', 'missing', 'skipped']


@dataclass(frozen=True, slots=True)
class MenuRow:
    """`sys_menu` 的一行，只取审计与守卫需要的列。"""

    id: int
    parent_id: int | None
    title: str
    name: str
    path: str | None
    component: str | None
    type: int
    status: int


@dataclass(frozen=True, slots=True)
class Resolution:
    """一行菜单的组件解析结果。

    - `ok`：严格命中，前端能真的打开；
    - `loose`：严格规则打不开，但同名目录下有 `index.vue`（多半是写法写错，**不是**能打开）；
    - `missing`：两种候选都没有，就是 404；
    - `skipped`：不参与比对（按钮行、layout 组件、component 为空）。
    """

    status: ResolutionStatus
    page_key: str | None
    file: Path | None = None
    loose_key: str | None = None


@dataclass(frozen=True, slots=True)
class ClassifiedMenu:
    """菜单行 + 解析结果 + A/B/C 分类。"""

    row: MenuRow
    resolution: Resolution
    categories: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditResult:
    """一次 dry-run 审计的完整结论。"""

    entries: tuple[ClassifiedMenu, ...]
    page_index_size: int
    doomed_ids: frozenset[int]
    orphan_ids: frozenset[int]
    empty_parent_ids: frozenset[int]
    unreferenced_pages: tuple[Path, ...]

    @property
    def total(self) -> int:
        """`sys_menu` 总行数。"""
        return len(self.entries)

    @property
    def button_rows(self) -> tuple[MenuRow, ...]:
        """按钮权限行（type=2），不参与页面比对，但会随父菜单被删。"""
        return tuple(entry.row for entry in self.entries if entry.row.type == BUTTON_MENU_TYPE)

    @property
    def removed_ids(self) -> frozenset[int]:
        """直接命中 + 连带孤儿的并集，即「删除后会消失的行」。"""
        return self.doomed_ids | self.orphan_ids

    @property
    def remaining(self) -> int:
        """删除后剩余菜单行数。"""
        return self.total - len(self.removed_ids)

    def by_category(self, category: str) -> tuple[ClassifiedMenu, ...]:
        """取某一类的全部条目（一行可同时属于多类）。"""
        return tuple(entry for entry in self.entries if category in entry.categories)


@dataclass(frozen=True, slots=True)
class GuardFailure:
    """守卫的一条失败记录。"""

    row: MenuRow
    page_key: str
    suggestion: str | None = None


@dataclass(frozen=True, slots=True)
class GuardReport:
    """守卫结论。`ok` 同时要求「有失败为 0」和「确实校验过东西」。"""

    checked: int
    skipped: int
    page_index_size: int
    failures: tuple[GuardFailure, ...]

    @property
    def vacuous(self) -> bool:
        """一行都没校验 / 页面索引为空——守卫本身失效，必须当失败处理。"""
        return self.checked == 0 or self.page_index_size == 0

    @property
    def ok(self) -> bool:
        """通过判据：没有失败，且不是空转。"""
        return not self.failures and not self.vacuous


# ---------------------------------------------------------------------------
# 组件路径映射
# ---------------------------------------------------------------------------


def normalize_view_path(path: str) -> str:
    """前端 `normalizeViewPath` 的等价移植。

    三步：去掉 `./` / `../` 前缀 → 补上开头的 `/` → 去掉开头的 `/views`。

    注意最后一步用的是前缀正则而非路径段匹配，前端就是这么写的（`/^\\/views/`），
    这里**故意保持一致**：如果哪天出现 `/viewsomething` 这种目录，两侧要一起错，不能只有守卫对。
    """
    normalized = _RELATIVE_PREFIX_RE.sub('', path)
    view_path = normalized if normalized.startswith('/') else f'/{normalized}'
    return _VIEWS_PREFIX_RE.sub('', view_path)


def strict_page_key(component: str | None) -> str | None:
    """算出前端真正会去 `pageMap` 里查的那个 key。

    返回 `None` 表示这一行不参与页面比对：`component` 为空，或者是 layout 组件
    （`BasicLayout` / `IFrameView` 走 layoutMap，不查 pageMap）。
    """
    if component is None:
        return None
    text = component.strip()
    if not text or text in LAYOUT_COMPONENTS:
        return None
    normalized = normalize_view_path(text)
    return normalized if normalized.endswith('.vue') else f'{normalized}.vue'


def loose_page_keys(component: str | None) -> tuple[str, ...]:
    """人工复核用的**宽松**候选：`/a/b` 除 `/a/b.vue` 外，作者可能本想指 `/a/b/index.vue`。

    ⚠️ 前端**不做**这个回落（`convertRoutes` 只拼一次 `.vue`），所以宽松命中 **不等于页面能打开**。
    它只用来把「写法写错、改一个字就能救」的行，从 A 类无页面里挑出来，避免当成废页直接删掉。
    """
    key = strict_page_key(component)
    if key is None or component is None:
        return ()
    # component 自己就写了 .vue，等于点名了具体文件，不存在「本想指 index」的可能
    if component.strip().endswith('.vue'):
        return ()
    stem = key[: -len('.vue')]
    if stem.endswith('/index'):
        return ()
    return (f'{stem}/index.vue',)


def page_key_for_file(frontend_src: Path, file: Path) -> str:
    """把一个 `.vue` 文件换算成 `pageMap` 的 key（等价于 glob key 过一遍 `normalizeViewPath`）。

    `views/a/b/index.vue` → `/a/b/index.vue`；`plugins/config/views/index.vue` →
    `/plugins/config/views/index.vue`（`plugins/` 前缀会保留，因为它不以 `/views` 开头）。
    """
    relative = file.resolve().relative_to(frontend_src.resolve()).as_posix()
    return normalize_view_path(relative)


def build_page_key_index(frontend_src: Path) -> dict[str, Path]:
    """扫描前端 src 下 `PAGE_ROOTS` 的每个根，构建 `pageMap` key → 文件 的索引。

    任何一个 glob 根缺失、或者一个 `.vue` 都没扫到，都直接抛 `PageIndexError`：
    路径写错必须炸，不能安静地把所有菜单判成无页面。
    """
    src = frontend_src.expanduser()
    if not src.is_dir():
        raise PageIndexError(f'前端 src 根目录不存在：{src}（用 --frontend-src 指到 apps/web-antdv-next/src）')

    index: dict[str, Path] = {}
    for root_name in PAGE_ROOTS:
        root = src / root_name
        if not root.is_dir():
            raise PageIndexError(f'缺少 pageMap 的 glob 根目录：{root}；前端 access.ts 会 glob 它，少扫即误判 404')
        for file in sorted(root.rglob('*.vue')):
            index[page_key_for_file(src, file)] = file

    if not index:
        raise PageIndexError(f'{src} 下一个 .vue 都没扫到，路径大概率写错了')
    return index


def resolve_component(component: str | None, page_index: Mapping[str, Path]) -> Resolution:
    """把一个 `component` 解析到前端文件。"""
    key = strict_page_key(component)
    if key is None:
        return Resolution(status='skipped', page_key=None)

    hit = page_index.get(key)
    if hit is not None:
        return Resolution(status='ok', page_key=key, file=hit)

    for loose in loose_page_keys(component):
        loose_hit = page_index.get(loose)
        if loose_hit is not None:
            return Resolution(status='loose', page_key=key, file=loose_hit, loose_key=loose)

    return Resolution(status='missing', page_key=key)


def default_frontend_src(start: Path | None = None) -> Path | None:
    """从本文件位置向上找父项目里的前端 src 根目录；找不到返回 `None`（由调用方报错，不猜）。

    主 clone（`hasn-cloud-backend/backend/scripts/ops_console/`）和 worktree
    （`hasn-cloud-backend/.worktrees/<名>/backend/scripts/ops_console/`）都能命中，
    因为两者向上都会走到父项目根目录 `huanxing-project/`。
    """
    anchor = (start if start is not None else Path(__file__)).resolve()
    for parent in anchor.parents:
        candidate = parent / FRONTEND_SRC_RELATIVE
        if candidate.is_dir():
            return candidate
    return None


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------


def component_module(component: str | None) -> str | None:
    """取 `component` 的第一段模块名，如 `/hasn/hasn_agents/index` → `hasn`。"""
    key = strict_page_key(component)
    if key is None:
        return None
    segments = [segment for segment in key.split('/') if segment]
    return segments[0] if len(segments) >= 2 else None


def is_app_surface(component: str | None) -> bool:
    """B 类判据：路径命中 AI-Native 应用前缀或脚手架/演示目录。"""
    return app_surface_reason(component) is not None


def app_surface_reason(component: str | None) -> str | None:
    """命中 B 类时返回原因（区分「应用面」和「脚手架/演示」），否则返回 `None`。"""
    key = strict_page_key(component)
    if key is None:
        return None
    for prefix in AI_NATIVE_APP_PREFIXES:
        if key.startswith(prefix):
            return f'AI-Native 应用面 {prefix.strip("/")}'
    for prefix in SCAFFOLD_PREFIXES:
        if key.startswith(prefix):
            return f'脚手架/演示目录 {prefix.strip("/")}'
    return None


def table_crud_target(component: str | None) -> tuple[str, str] | None:
    """把 `/<模块>/<表名>/index` 拆成 `(模块, 表名)`；形状不符返回 `None`。"""
    key = strict_page_key(component)
    if key is None:
        return None
    matched = _TABLE_CRUD_RE.match(key)
    if matched is None:
        return None
    return matched.group('module'), matched.group('table')


def table_crud_reason(component: str | None) -> str | None:
    """命中 C 类时返回原因，否则返回 `None`。"""
    target = table_crud_target(component)
    if target is None:
        return None
    module, table = target
    if module in RELATION_COMMUNITY_MODULES:
        return f'关系/社区模块 {module}'
    if table in RELATION_COMMUNITY_TABLES:
        return f'关系/社区表 {table}'
    return None


def classify_row(row: MenuRow, page_index: Mapping[str, Path]) -> ClassifiedMenu:
    """给单行菜单做解析 + A/B/C 分类。一行可以同时属于多类。"""
    if row.type == BUTTON_MENU_TYPE:
        return ClassifiedMenu(
            row=row,
            resolution=Resolution(status='skipped', page_key=None),
            categories=(),
            notes=('按钮权限行，不参与页面比对；父菜单被删时它会成为孤儿',),
        )

    resolution = resolve_component(row.component, page_index)
    categories: list[str] = []
    notes: list[str] = []

    if resolution.status == 'missing':
        categories.append(CATEGORY_NO_PAGE)
        notes.append(f'找不到 {resolution.page_key}')
    elif resolution.status == 'loose':
        notes.append(
            f'严格规则找不到 {resolution.page_key}，但存在 {resolution.loose_key}；'
            f'前端不会回落，实际仍是 404——多半是 component 少写了 /index，改写法即可救，不要直接删'
        )
    elif resolution.status == 'skipped':
        if row.component is None or not row.component.strip():
            notes.append('component 为空，不参与页面比对')
        else:
            notes.append(f'layout 组件 {row.component.strip()}，走 layoutMap 不查 pageMap')

    app_reason = app_surface_reason(row.component)
    if app_reason is not None:
        categories.append(CATEGORY_APP_SURFACE)
        notes.append(app_reason)

    crud_reason = table_crud_reason(row.component)
    if crud_reason is not None:
        categories.append(CATEGORY_TABLE_CRUD)
        notes.append(crud_reason)

    return ClassifiedMenu(row=row, resolution=resolution, categories=tuple(categories), notes=tuple(notes))


def _descendant_ids(rows: Sequence[MenuRow], seed_ids: frozenset[int]) -> frozenset[int]:
    """求 `seed_ids` 的全部后代 ID（不含 seed 自身）。"""
    children: dict[int, list[int]] = {}
    for row in rows:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row.id)

    found: set[int] = set()
    pending = list(seed_ids)
    while pending:
        current = pending.pop()
        for child_id in children.get(current, ()):
            if child_id not in found and child_id not in seed_ids:
                found.add(child_id)
                pending.append(child_id)
    return frozenset(found)


def audit_menus(rows: Sequence[MenuRow], page_index: Mapping[str, Path]) -> AuditResult:
    """对全量菜单行做一次 dry-run 审计。纯计算，不产生任何副作用。"""
    entries = tuple(classify_row(row, page_index) for row in rows)
    doomed_ids = frozenset(entry.row.id for entry in entries if entry.categories)
    orphan_ids = _descendant_ids(rows, doomed_ids)
    removed = doomed_ids | orphan_ids

    # 删完之后一个子节点都不剩的目录：它自己没被判删，但留着就是空目录。
    surviving = [row for row in rows if row.id not in removed]
    surviving_parent_ids = {row.parent_id for row in surviving if row.parent_id is not None}
    empty_parent_ids = frozenset(
        row.id for row in surviving if row.type == 0 and row.id not in surviving_parent_ids
    )

    # 反向清单：存在 .vue 文件但没有任何菜单行指向它。宽松命中也算「被指向」，
    # 否则一个写错路径的菜单会让真页面同时出现在 A 类和「无菜单页面」里，重复计数。
    referenced: set[str] = set()
    for entry in entries:
        if entry.resolution.status == 'ok' and entry.resolution.page_key is not None:
            referenced.add(entry.resolution.page_key)
        elif entry.resolution.status == 'loose' and entry.resolution.loose_key is not None:
            referenced.add(entry.resolution.loose_key)
    unreferenced_pages = tuple(file for key, file in sorted(page_index.items()) if key not in referenced)

    return AuditResult(
        entries=entries,
        page_index_size=len(page_index),
        doomed_ids=doomed_ids,
        orphan_ids=orphan_ids,
        empty_parent_ids=empty_parent_ids,
        unreferenced_pages=unreferenced_pages,
    )


# ---------------------------------------------------------------------------
# 守卫
# ---------------------------------------------------------------------------


def check_menu_pages(rows: Sequence[MenuRow], page_index: Mapping[str, Path]) -> GuardReport:
    """守卫判定：能被下发到侧边栏的每一行，其 `component` 都必须能在前端找到 `.vue`。

    校验范围 = `SIDEBAR_MENU_TYPES`（与后端 `get_sidebar` 的 `type__in` 对齐）且 `component`
    非空、非 layout 组件。按钮行（type=2）component 恒为空，天然落在范围外。

    **不按 `status` 过滤**：`get_sidebar` 也不过滤，`status=0` 只是让前端把组件换成 403 页
    （`menuVisibleWithForbidden`），行照样下发，component 写错照样在控制台报错。

    宽松命中（`loose`）一律算失败——前端不做 `/index` 回落，它跟 `missing` 一样打不开，
    只是修法不同，所以在 `suggestion` 里给出改写建议。
    """
    checked = 0
    skipped = 0
    failures: list[GuardFailure] = []

    for row in rows:
        if row.type not in SIDEBAR_MENU_TYPES:
            skipped += 1
            continue
        resolution = resolve_component(row.component, page_index)
        page_key = resolution.page_key
        if resolution.status == 'skipped' or page_key is None:
            skipped += 1
            continue

        checked += 1
        if resolution.status == 'ok':
            continue

        suggestion = (
            f'把 component 改成 {resolution.loose_key[: -len(".vue")]}'
            if resolution.status == 'loose' and resolution.loose_key is not None
            else None
        )
        failures.append(GuardFailure(row=row, page_key=page_key, suggestion=suggestion))

    return GuardReport(checked=checked, skipped=skipped, page_index_size=len(page_index), failures=tuple(failures))


# ---------------------------------------------------------------------------
# 菜单 SQL 文件侧的组件路径（用于和库里的行做量级对照）
# ---------------------------------------------------------------------------


def extract_menu_sql_components(sql_dir: Path) -> dict[str, list[Path]]:
    """从 `backend/sql/**/*menu*.sql` 里抽出组件路径 → 出现的文件列表。

    这是**启发式正则**（只认以 `/index` 或 `.vue` 结尾的单引号字面量），不是 SQL 解析；
    抽出的数量只用来和库里的实际行做量级对照，帮人发现「SQL 文件里有但库里没有」的部分。
    施工清单 §1 的「313 条菜单路径」就是从这些文件推算的，与库里的行数不是一回事。
    """
    found: dict[str, list[Path]] = {}
    if not sql_dir.is_dir():
        raise PageIndexError(f'菜单 SQL 目录不存在：{sql_dir}')
    for file in sorted(sql_dir.rglob('*.sql')):
        if 'menu' not in file.name.lower():
            continue
        text = file.read_text(encoding='utf-8', errors='replace')
        for component in set(_SQL_COMPONENT_RE.findall(text)):
            found.setdefault(component, []).append(file)
    return found


# ---------------------------------------------------------------------------
# CSV 记录（真正写文件在 audit_menus.py，这里只做纯变换，便于测试）
# ---------------------------------------------------------------------------

CSV_FIELDS: tuple[str, ...] = (
    'id',
    'parent_id',
    'title',
    'name',
    'path',
    'component',
    'type',
    'status',
    'resolve_status',
    'resolved_file',
    'categories',
    'action',
    'notes',
)


def _action_of(entry: ClassifiedMenu, result: AuditResult) -> str:
    """这一行在「按清单删除」后会怎样。"""
    if entry.row.id in result.doomed_ids:
        return '待删（命中 A/B/C）'
    if entry.row.id in result.orphan_ids:
        return '连带删除（父节点被删）'
    if entry.row.id in result.empty_parent_ids:
        return '删后成为空目录（需人工确认是否一并删）'
    if entry.resolution.status == 'loose':
        return '待人工确认（写法疑似写错，改 component 即可救）'
    return '保留'


def audit_csv_records(result: AuditResult) -> list[dict[str, str]]:
    """把审计结论摊平成 CSV 行。输出**全量菜单行**并用 `action` 列区分去留，便于人工整表复核。"""
    records: list[dict[str, str]] = []
    for entry in result.entries:
        row = entry.row
        records.append({
            'id': str(row.id),
            'parent_id': '' if row.parent_id is None else str(row.parent_id),
            'title': row.title,
            'name': row.name,
            'path': row.path or '',
            'component': row.component or '',
            'type': str(row.type),
            'status': str(row.status),
            'resolve_status': entry.resolution.status,
            'resolved_file': '' if entry.resolution.file is None else str(entry.resolution.file),
            'categories': '|'.join(entry.categories),
            'action': _action_of(entry, result),
            'notes': '；'.join(entry.notes),
        })
    return records


def unclassified_modules(result: AuditResult) -> dict[str, int]:
    """既没进 A/B/C、组件也解析成功的行，按模块聚合，供人工判断是否漏了应用前缀。"""
    counter: dict[str, int] = {}
    for entry in result.entries:
        if entry.categories or entry.resolution.status != 'ok':
            continue
        module = component_module(entry.row.component)
        if module is None:
            continue
        counter[module] = counter.get(module, 0) + 1
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def menu_type_counts(rows: Iterable[MenuRow]) -> dict[int, int]:
    """按 `type` 统计行数（0 目录 / 1 菜单 / 2 按钮 / 3 内嵌 / 4 外链）。"""
    counter: dict[int, int] = {}
    for row in rows:
        counter[row.type] = counter.get(row.type, 0) + 1
    return dict(sorted(counter.items()))
