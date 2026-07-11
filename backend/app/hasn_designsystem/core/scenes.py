"""组件画廊「场景标准」与确定性场景覆盖检测（DSGAL）。

设计系统的组件画廊不再只是一堆散装 UI 原语，而是按**交付物场景**组织：品牌网站 / 演示文稿 /
产品海报 / 移动端。每个场景有一套「标准组件」（必须齐的那几件），分身产出 ``components.html`` 时用
结构标记声明每件组件属于哪个场景，本模块**确定性**统计每个场景标准组件的到位情况，供：

- ``extract_components`` 在 manifest 里透出 ``scenes[]`` 覆盖报告（纯函数，只看 HTML）；
- 详情页交叉 owner 的 ``required_scenes``（存云端库，非本模块职责）渲染「必须/可选 · 已配齐/缺 X」；
- 完成卡软提示「品牌网站 3/5 · 缺 CTA/页脚」（福仔拍板：软提示，不阻断发卡）。

# 标记约定（分身在 components.html 里写）
- 场景容器：``<section data-ds-scene="brand_website"> … </section>``
- 场景内组件：``<div data-ds-component="hero"> … </div>``（归属到文档序上最近的 ``data-ds-scene``）
- 或场景限定写法：``<div data-ds-component="brand_website.hero">``（值里带 ``场景.组件`` 前缀，覆盖当前场景）

# 纯函数约定
只看输入 HTML、无 IO、无时钟；同输入同输出（与 Rust ``hasn-designsystem-core::components`` 逐字节一致，
过渡期两引擎并行须严格对齐——见 ``components.py`` 顶部说明）。
"""

from __future__ import annotations

import re

from typing import NamedTuple

# 场景覆盖 schema 版本（并入 components.manifest，随 COMPONENTS_MANIFEST_SCHEMA_VERSION 一起演进）。
SCENE_COVERAGE_SCHEMA_VERSION = 1


class SceneComponent(NamedTuple):
    """一件标准组件：机器 key（进标记/manifest）+ 中文展示名（给人看）。"""

    key: str
    label: str


class SceneStandard(NamedTuple):
    """一个交付物场景的标准：必须齐的组件 + 可选加分组件。"""

    id: str
    label: str
    required: tuple[SceneComponent, ...]
    optional: tuple[SceneComponent, ...]


# ── 四场景标准（单一事实源·福仔 2026-07-10 拍板）─────────────────────────────
# 品牌网站默认必须；演示文稿/产品海报/移动端可选（owner 派发时可勾为必须）。
# ⚠️ 改这里必须同步 Rust `hasn-designsystem-core::components` 的场景标准 + webui SCENE_STANDARDS +
#    hub designsystem-authoring 技能 + doc20，四处一致（多语言各一份、语义严格对齐）。
SCENE_STANDARDS: tuple[SceneStandard, ...] = (
    SceneStandard(
        id='brand_website',
        label='品牌网站',
        required=(
            SceneComponent('nav', '导航栏'),
            SceneComponent('hero', 'Hero 首屏'),
            SceneComponent('features', '特性区'),
            SceneComponent('cta', '行动号召 CTA'),
            SceneComponent('footer', '页脚'),
        ),
        optional=(
            SceneComponent('pricing', '定价表'),
            SceneComponent('testimonial', '客户评价'),
            SceneComponent('faq', '常见问题 FAQ'),
        ),
    ),
    SceneStandard(
        id='deck',
        label='演示文稿',
        required=(
            SceneComponent('cover', '封面页'),
            SceneComponent('section', '章节分隔页'),
            SceneComponent('bullets', '要点页'),
            SceneComponent('chart', '数据图表页'),
            SceneComponent('closing', '结束页'),
        ),
        optional=(),
    ),
    SceneStandard(
        id='poster',
        label='产品海报',
        required=(
            SceneComponent('hero_poster', '主视觉海报'),
            SceneComponent('info_card', '信息卡片'),
            SceneComponent('social_square', '社媒方图'),
        ),
        optional=(),
    ),
    SceneStandard(
        id='mobile',
        label='移动端',
        required=(
            SceneComponent('mobile_nav', '顶部导航'),
            SceneComponent('tab_bar', '底部 Tab 栏'),
            SceneComponent('list_card', '列表卡片'),
            SceneComponent('form', '表单'),
            SceneComponent('button_group', '按钮组'),
        ),
        optional=(),
    ),
)

# 默认必须场景（新建设计系统时的 required_scenes 初值）。
DEFAULT_REQUIRED_SCENES: tuple[str, ...] = ('brand_website',)

_SCENE_BY_ID: dict[str, SceneStandard] = {s.id: s for s in SCENE_STANDARDS}

# 匹配 data-ds-scene / data-ds-component 属性（单双引号皆可；值为简单标识符，不含引号）。
# finditer 按文档序返回，供「组件归属到最近场景」的顺序折叠（与 Rust regex 语义对齐）。
_MARKER_RE = re.compile(r"""data-ds-(scene|component)\s*=\s*["']([^"']*)["']""")


def known_scene_ids() -> list[str]:
    """全部已知场景 id（声明序）。"""
    return [s.id for s in SCENE_STANDARDS]


def is_known_scene(scene_id: str) -> bool:
    return scene_id in _SCENE_BY_ID


def _fold_present_components(html: str) -> dict[str, set[str]]:
    """按文档序折叠标记 → {场景 id: 已到位的标准组件 key 集合}。

    组件归属：值含 ``.`` 时按 ``场景.组件`` 显式归属；否则归属到文档序上最近的 ``data-ds-scene``。
    只收「已知场景的已知组件」（required∪optional），未知一律忽略（零 fake，不臆造覆盖）。
    """
    present: dict[str, set[str]] = {}
    current_scene: str | None = None
    for match in _MARKER_RE.finditer(html):
        kind = match.group(1)
        value = match.group(2).strip().lower()
        if kind == 'scene':
            current_scene = value if value else None
            continue
        # kind == 'component'
        if '.' in value:
            scene_id, _, comp = value.partition('.')
            scene_id = scene_id.strip()
            comp = comp.strip()
        else:
            scene_id = current_scene or ''
            comp = value
        std = _SCENE_BY_ID.get(scene_id)
        if std is None or not comp:
            continue
        known = {c.key for c in std.required} | {c.key for c in std.optional}
        if comp in known:
            present.setdefault(scene_id, set()).add(comp)
    return present


def detect_scenes(html: str) -> list[dict]:
    """从 components.html 检测场景覆盖 → 覆盖报告列表（纯函数，只看 HTML）。

    只为「有至少一件标准组件到位」的场景产出条目（诚实反映分身实际产出；某场景一件没标 → 不出条目，
    由详情页交叉 required_scenes 显示「缺全部」）。条目按 SCENE_STANDARDS 声明序，字段 camelCase 对齐
    manifest 既有风格；``complete`` = 该场景全部必须组件到位。
    """
    present = _fold_present_components(html)
    scenes: list[dict] = []
    for std in SCENE_STANDARDS:
        got = present.get(std.id)
        if not got:
            continue
        required_keys = [c.key for c in std.required]
        present_required = [k for k in required_keys if k in got]
        missing = [k for k in required_keys if k not in got]
        optional_present = [c.key for c in std.optional if c.key in got]
        scenes.append({
            'id': std.id,
            'label': std.label,
            'requiredComponents': required_keys,
            'presentComponents': present_required,
            'missingComponents': missing,
            'optionalPresent': optional_present,
            'complete': not missing,
        })
    return scenes
