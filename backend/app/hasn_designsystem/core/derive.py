"""派生纯函数（Python 移植 hasn-designsystem-core `derive.rs`）：
``tokens.css`` → ``design-tokens.json``（``hasn-design-tokens/v1``）+ ``tailwind-v4.css``（``@theme`` 映射）。
"""

from __future__ import annotations

import json

from typing import Any

from . import css
from .contract import validate

# Tailwind v4 @theme 变量名 → hasn token 名的映射（移植 `TAILWIND_V4_THEME_BINDINGS`，顺序一致）。
TAILWIND_V4_THEME_BINDINGS: tuple[tuple[str, str], ...] = (
    ('--color-bg', '--bg'),
    ('--color-surface', '--surface'),
    ('--color-surface-warm', '--surface-warm'),
    ('--color-fg', '--fg'),
    ('--color-fg-2', '--fg-2'),
    ('--color-muted', '--muted'),
    ('--color-meta', '--meta'),
    ('--color-border', '--border'),
    ('--color-border-soft', '--border-soft'),
    ('--color-accent', '--accent'),
    ('--color-accent-on', '--accent-on'),
    ('--color-accent-hover', '--accent-hover'),
    ('--color-accent-active', '--accent-active'),
    ('--color-success', '--success'),
    ('--color-warn', '--warn'),
    ('--color-danger', '--danger'),
    ('--font-display', '--font-display'),
    ('--font-body', '--font-body'),
    ('--font-sans', '--font-body'),
    ('--font-mono', '--font-mono'),
    ('--text-xs', '--text-xs'),
    ('--text-sm', '--text-sm'),
    ('--text-base', '--text-base'),
    ('--text-lg', '--text-lg'),
    ('--text-xl', '--text-xl'),
    ('--text-2xl', '--text-2xl'),
    ('--text-3xl', '--text-3xl'),
    ('--text-4xl', '--text-4xl'),
    ('--leading-body', '--leading-body'),
    ('--leading-tight', '--leading-tight'),
    ('--tracking-display', '--tracking-display'),
    ('--spacing-1', '--space-1'),
    ('--spacing-2', '--space-2'),
    ('--spacing-3', '--space-3'),
    ('--spacing-4', '--space-4'),
    ('--spacing-5', '--space-5'),
    ('--spacing-6', '--space-6'),
    ('--spacing-8', '--space-8'),
    ('--spacing-12', '--space-12'),
    ('--spacing-section-desktop', '--section-y-desktop'),
    ('--spacing-section-tablet', '--section-y-tablet'),
    ('--spacing-section-phone', '--section-y-phone'),
    ('--radius-sm', '--radius-sm'),
    ('--radius-md', '--radius-md'),
    ('--radius-lg', '--radius-lg'),
    ('--radius-pill', '--radius-pill'),
    ('--shadow-flat', '--elev-flat'),
    ('--shadow-ring', '--elev-ring'),
    ('--shadow-raised', '--elev-raised'),
    ('--shadow-focus-ring', '--focus-ring'),
    ('--duration-fast', '--motion-fast'),
    ('--duration-base', '--motion-base'),
    ('--ease-standard', '--ease-standard'),
    ('--container-max', '--container-max'),
    ('--spacing-container-desktop', '--container-gutter-desktop'),
    ('--spacing-container-tablet', '--container-gutter-tablet'),
    ('--spacing-container-phone', '--container-gutter-phone'),
)

_COLOR_TOKEN_NAMES: frozenset[str] = frozenset({
    '--bg',
    '--surface',
    '--surface-warm',
    '--fg',
    '--fg-2',
    '--muted',
    '--meta',
    '--border',
    '--border-soft',
    '--accent',
    '--accent-on',
    '--accent-hover',
    '--accent-active',
    '--success',
    '--warn',
    '--danger',
})

_DIMENSION_PREFIXES = ('--text-', '--space-', '--section-y-', '--radius-', '--container-', '--tracking-')


def infer_design_token_type(name: str) -> str:
    """推断 design token 类型（移植 `inferDesignTokenType`）。"""
    if name in _COLOR_TOKEN_NAMES:
        return 'color'
    if name.startswith('--font-'):
        return 'fontFamily'
    if name.startswith('--leading-'):
        return 'number'
    if name == '--ease-standard':
        return 'cubicBezier'
    if name.startswith('--motion-'):
        return 'duration'
    if name.startswith('--elev-') or name == '--focus-ring':
        return 'shadow'
    if any(name.startswith(prefix) for prefix in _DIMENSION_PREFIXES):
        return 'dimension'
    return 'other'


def render_design_tokens_json(
    token_bindings: list[dict[str, Any]],
    summary: dict[str, Any],
    generated_at: str,
) -> str:
    """渲染 design-tokens.json（移植 `renderDesignTokensJson`，标识 ``hasn-design-tokens/v1``）。

    输出 **2 空格 pretty + 尾换行**，对齐 serde ``to_string_pretty``；``ensure_ascii=False``
    使非 ASCII 不转义（与 serde 一致）。
    """
    tokens: list[dict[str, Any]] = []
    for binding in token_bindings:
        entry: dict[str, Any] = {
            'name': binding['name'],
            'value': binding['value'],
            'type': infer_design_token_type(binding['name']),
            'layer': binding['layer'],
            'confidence': binding['confidence'],
            'reason': binding['reason'],
            'sources': binding['sources'],
        }
        if 'sourceName' in binding:
            entry['sourceName'] = binding['sourceName']
        tokens.append(entry)

    payload = {
        'schemaVersion': 1,
        'format': 'hasn-design-tokens/v1',
        'contract': 'TOKEN_SCHEMA',
        'generatedAt': generated_at,
        'source': {
            'tokensCss': 'tokens.css',
            'tokenContractReport': 'source/token-contract.report.json',
        },
        'summary': summary,
        'tokens': tokens,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + '\n'


def render_tailwind_v4_css(declared_names: list[str]) -> str:
    """渲染 tailwind-v4.css（移植 `renderTailwindV4Css`）：只对**已声明**的 token 输出 @theme 映射。"""
    declared = set(declared_names)
    lines = [
        '/* Derived from tokens.css. Keep tokens.css as the source of truth. */',
        '@import "tailwindcss";',
        '@import "./tokens.css";',
        '',
        '@theme {',
    ]
    for tailwind_name, hasn_token in TAILWIND_V4_THEME_BINDINGS:
        if hasn_token in declared:
            lines.append(f'  {tailwind_name}: var({hasn_token});')
    lines.extend(('}', ''))
    return '\n'.join(lines)


def derive(tokens_css: str, generated_at: str) -> dict[str, str]:
    """从一份 tokens.css 派生 ``design-tokens.json`` + ``tailwind-v4.css``（独立可调，离线纯函数）。

    内部复用 :func:`validate` 得到绑定与评分摘要。返回
    ``{'design_tokens_json': ..., 'tailwind_v4_css': ...}``。
    """
    report = validate(tokens_css, generated_at)
    declared_names = [name for name, _ in css.parse_token_declarations(tokens_css)]
    design_tokens_json = render_design_tokens_json(report['tokens'], report['summary'], generated_at)
    tailwind_v4_css = render_tailwind_v4_css(declared_names)
    return {'design_tokens_json': design_tokens_json, 'tailwind_v4_css': tailwind_v4_css}
