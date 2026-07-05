"""四层 token 契约 schema（Python 移植 hasn-designsystem-core `schema.rs`，逐项对齐）。

TOOLMIG（2026-07-04）：设计系统 4 个确定性纯函数（compile/derive/validate/extract_components）
从 hasn-node 本地 Rust `hasn-designsystem-core` 迁到云端 Python，云端分身经 platform MCP 可用，
Rust 侧逐渐退役 → 收敛到 Python 单一实现源。本文件是 token 四层归类的权威 schema。

每个共享 token 属于四层之一，由「谁决定值」「省略时怎么办」区分：
- ``A1-identity``：品牌身份值，必填，无 fallback（``--bg`` ``--fg`` ``--accent`` 字体栈）。
- ``A1-structure``：结构决策，必填，每个品牌自定（字号阶梯、布局栅格、区域节奏）。
- ``A2``：必填，但 schema 自带 fallback，派生脚本可回填。
- ``B-slot``：可选 schema 槽，品牌无更细层级时别名折叠到兄弟 token（``var(--sibling)``）。
- ``C-extension``：品牌私有 token，进 per-brand allowlist（经 :func:`is_allowed_extension` 判定）。
"""

from __future__ import annotations

from dataclasses import dataclass

# token 四层归类的契约字符串（与 Rust `TokenLayer::as_str` / open-design 字面量一致）。
LAYER_A1_IDENTITY = 'A1-identity'
LAYER_A1_STRUCTURE = 'A1-structure'
LAYER_A2 = 'A2'
LAYER_B_SLOT = 'B-slot'

# A1 族（identity 或 structure）——评分时 A1 覆盖率的口径。
_A1_LAYERS = frozenset({LAYER_A1_IDENTITY, LAYER_A1_STRUCTURE})


def layer_is_a1(layer: str) -> bool:
    """是否属于 A1 族（identity 或 structure）。"""
    return layer in _A1_LAYERS


@dataclass(frozen=True)
class TokenSpec:
    """单个 schema token 的规约（对齐 Rust `TokenSpec`）。"""

    name: str
    """CSS 自定义属性名（含 ``--`` 前缀）。"""
    layer: str
    """归属层（四个契约字符串之一）。"""
    description: str
    """一行说明（供文档/报告）。"""
    fallback: str | None = None
    """仅 A2：派生脚本回填的默认值。"""
    alias_to: str | None = None
    """仅 B-slot：无更细层级时折叠到的兄弟表达式（通常 ``var(--name)``）。"""


def _ident(name: str, description: str) -> TokenSpec:
    return TokenSpec(name=name, layer=LAYER_A1_IDENTITY, description=description)


def _structure(name: str, description: str) -> TokenSpec:
    return TokenSpec(name=name, layer=LAYER_A1_STRUCTURE, description=description)


def _a2(name: str, description: str, fallback: str) -> TokenSpec:
    return TokenSpec(name=name, layer=LAYER_A2, description=description, fallback=fallback)


def _b_slot(name: str, description: str, alias_to: str) -> TokenSpec:
    return TokenSpec(name=name, layer=LAYER_B_SLOT, description=description, alias_to=alias_to)


# 完整 token schema（首版固定，D7）。每套设计系统的 tokens.css 必须声明此处每一项。
# 顺序按视觉栈分组（surface→text→border→accent→semantic→typography→spacing→radius→
# elevation→focus→motion→layout），便于人审，而非按层排。**顺序与 Rust 逐条一致**
# （影响 bindings 顺序 → tokens.css 行序）。
TOKEN_SCHEMA: tuple[TokenSpec, ...] = (
    # ── Surface ──
    _ident('--bg', 'Page background — defines the brand canvas.'),
    _ident('--surface', 'Card / lifted container background.'),
    _b_slot('--surface-warm', 'Tertiary surface tier (kami warm-sand).', 'var(--surface)'),
    # ── Foreground ──
    _ident('--fg', 'Primary text color.'),
    _b_slot('--fg-2', 'Secondary text tier (kami dark-warm).', 'var(--fg)'),
    _ident('--muted', 'Subtext / captions.'),
    _b_slot('--meta', 'Tertiary FG / metadata tier (kami stone).', 'var(--muted)'),
    # ── Border ──
    _ident('--border', 'Default border / card edge.'),
    _b_slot(
        '--border-soft',
        'Inner row separator that should not visually compete.',
        'var(--border)',
    ),
    # ── Accent ──
    _ident('--accent', 'Brand accent. <=2 visible uses per screen (lint enforced).'),
    _a2('--accent-on', 'FG when --accent is the bg.', '#ffffff'),
    _a2(
        '--accent-hover',
        'Hover state for elements using --accent as bg.',
        'color-mix(in oklab, var(--accent), black 8%)',
    ),
    _a2(
        '--accent-active',
        'Active state for elements using --accent as bg.',
        'color-mix(in oklab, var(--accent), black 14%)',
    ),
    # ── Semantic ──
    _a2('--success', 'Success state.', '#16a34a'),
    _a2('--warn', 'Warning state.', '#eab308'),
    _a2('--danger', 'Danger state.', '#dc2626'),
    # ── Typography — fonts ──
    _ident('--font-display', 'Display / heading font stack.'),
    _ident('--font-body', 'Body font stack.'),
    _a2(
        '--font-mono',
        'Monospace font stack — used by kbd, code, tabular metrics.',
        'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, monospace',
    ),
    # ── Typography — type scale ──
    _structure('--text-xs', 'Type scale step — extra small (≈11–12px).'),
    _structure('--text-sm', 'Type scale step — small (≈12–14px).'),
    _structure('--text-base', 'Type scale step — body baseline.'),
    _structure('--text-lg', 'Type scale step — H3 / featured body.'),
    _structure('--text-xl', 'Type scale step — H2.'),
    _structure('--text-2xl', 'Type scale step — section title.'),
    _structure('--text-3xl', 'Type scale step — H1.'),
    _structure('--text-4xl', 'Type scale step — display / hero.'),
    # ── Typography — leading & tracking ──
    _structure('--leading-body', 'Line-height for reading body.'),
    _structure('--leading-tight', 'Line-height for headings.'),
    _structure('--tracking-display', 'Letter-spacing applied to display sizes.'),
    # ── Spacing — base scale ──
    _a2('--space-1', 'Base spacing — 4px tier.', '4px'),
    _a2('--space-2', 'Base spacing — 8px tier.', '8px'),
    _a2('--space-3', 'Base spacing — 12px tier.', '12px'),
    _a2('--space-4', 'Base spacing — 16px tier.', '16px'),
    _a2('--space-5', 'Base spacing — 20px tier.', '20px'),
    _a2('--space-6', 'Base spacing — 24px tier.', '24px'),
    _a2('--space-8', 'Base spacing — 32px tier.', '32px'),
    _a2('--space-12', 'Base spacing — 48px tier.', '48px'),
    # ── Section rhythm ──
    _structure('--section-y-desktop', 'Vertical padding between sections — desktop.'),
    _structure('--section-y-tablet', 'Vertical padding between sections — tablet.'),
    _structure('--section-y-phone', 'Vertical padding between sections — phone.'),
    # ── Radius ──
    _a2('--radius-sm', 'Small radius — buttons, inputs, chips.', '8px'),
    _a2('--radius-md', 'Medium radius — cards, modals.', '12px'),
    _a2('--radius-lg', 'Large radius — featured containers.', '16px'),
    _a2('--radius-pill', 'Pill radius — avatars, badges.', '9999px'),
    # ── Elevation ──
    _a2('--elev-flat', 'No elevation.', 'none'),
    _a2('--elev-ring', 'Hairline ring (1px box-shadow border).', '0 0 0 1px var(--border)'),
    _a2(
        '--elev-raised',
        'Raised surface (blur or whisper).',
        '0 2px 8px color-mix(in oklab, var(--fg), transparent 92%)',
    ),
    # ── Focus ──
    _a2(
        '--focus-ring',
        'Keyboard focus indicator.',
        '0 0 0 3px color-mix(in oklab, var(--accent), transparent 70%)',
    ),
    # ── Motion ──
    _a2('--motion-fast', 'Hover / micro-state duration.', '150ms'),
    _a2('--motion-base', 'General state-change duration.', '200ms'),
    _a2('--ease-standard', 'Standard easing curve.', 'cubic-bezier(0.2, 0, 0, 1)'),
    # ── Layout ──
    _structure('--container-max', 'Max content container width.'),
    _structure('--container-gutter-desktop', 'Container side gutter — desktop.'),
    _structure('--container-gutter-tablet', 'Container side gutter — tablet.'),
    _structure('--container-gutter-phone', 'Container side gutter — phone.'),
)

# 任意品牌都允许的 C-extension token 名前缀族（如 kami 的 --tag-bg-* 预混标签色）。
BRAND_EXTENSION_PREFIXES: tuple[str, ...] = ('--tag-bg-',)

# schema 全部 token 名集合（快查）。
SCHEMA_NAMES: frozenset[str] = frozenset(spec.name for spec in TOKEN_SCHEMA)

_SPEC_BY_NAME: dict[str, TokenSpec] = {spec.name: spec for spec in TOKEN_SCHEMA}


def all_schema_names() -> list[str]:
    """schema 全部 token 名（顺序与 :data:`TOKEN_SCHEMA` 一致）。"""
    return [spec.name for spec in TOKEN_SCHEMA]


def spec_for(name: str) -> TokenSpec | None:
    """按名取 spec。"""
    return _SPEC_BY_NAME.get(name)


def is_allowed_extension(name: str, allowed: list[str]) -> bool:
    """是否为允许的 C-extension：命中通用前缀，或在调用方提供的 per-brand allowlist 内。"""
    if any(name.startswith(prefix) for prefix in BRAND_EXTENSION_PREFIXES):
        return True
    return any(allowed_name == name for allowed_name in allowed)
