"""token 契约编译 + 校验 + 评分（Python 移植 hasn-designsystem-core `contract.rs`，语义对齐）。

- :func:`compile`：把导入/原始 source token 绑定到 :data:`TOKEN_SCHEMA`
  （exact→high / role-hint→medium / B-slot→alias / fallback→fallback|low），
  渲染标准 tokens.css，给出评分报告。
- :func:`validate`：对一份已有 tokens.css（+ 可选 components.html）做四层契约校验 + 评分。

**JSON 形状与 Rust serde 逐字段对齐**（camelCase summary / kebab-case grade / lowercase confidence /
层键 A1-identity·B-slot / selfCheck / sourceName skip-if-none / LayerStat 内部 snake_case）。
"""

from __future__ import annotations

import math

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import css
from .schema import (
    LAYER_A2,
    LAYER_B_SLOT,
    SCHEMA_NAMES,
    TOKEN_SCHEMA,
    TokenSpec,
    is_allowed_extension,
    layer_is_a1,
)

DEFAULT_BODY_FONT = 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'

# token 证据置信度（与 Rust `Confidence` serde lowercase 一致）。
CONF_HIGH = 'high'
CONF_MEDIUM = 'medium'
CONF_LOW = 'low'
CONF_FALLBACK = 'fallback'
CONF_ALIAS = 'alias'

_SOURCE_BACKED = frozenset({CONF_HIGH, CONF_MEDIUM})
_FALLBACK_OR_LOW = frozenset({CONF_FALLBACK, CONF_LOW})

# 评分等级（与 Rust `Grade` serde kebab-case 一致）。
GRADE_EXCELLENT = 'excellent'
GRADE_USABLE = 'usable'
GRADE_NEEDS_REVIEW = 'needs-review'
GRADE_NEEDS_REBUILD = 'needs-rebuild'


@dataclass(frozen=True)
class SourceToken:
    """一个原始/导入来源 token（shadcn cssVars / GitHub 扫描 / 截图扫色 / 已有 tokens.css 声明）。"""

    name: str
    value: str
    source: str
    line: int | None = None
    usage: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Binding:
    """单个 token 的最终绑定（含层、值、置信度、血缘）。"""

    name: str
    layer: str
    value: str
    confidence: str
    reason: str
    sources: list[str]
    source_name: str | None = None


def _default_token_value(name: str) -> str | None:
    """导入器对缺源 A1 token 的保守默认值（移植 ``DEFAULT_TOKEN_VALUES``）。"""
    return _DEFAULT_TOKEN_VALUES.get(name)


_DEFAULT_TOKEN_VALUES: dict[str, str] = {
    '--bg': '#f8fafc',
    '--surface': '#ffffff',
    '--fg': '#111827',
    '--muted': '#6b7280',
    '--border': '#d1d5db',
    '--accent': '#2563eb',
    '--font-display': DEFAULT_BODY_FONT,
    '--font-body': DEFAULT_BODY_FONT,
    '--text-xs': '0.75rem',
    '--text-sm': '0.875rem',
    '--text-base': '1rem',
    '--text-lg': '1.125rem',
    '--text-xl': '1.375rem',
    '--text-2xl': '1.75rem',
    '--text-3xl': '2.25rem',
    '--text-4xl': '3rem',
    '--leading-body': '1.55',
    '--leading-tight': '1.15',
    '--tracking-display': '0',
    '--section-y-desktop': '96px',
    '--section-y-tablet': '68px',
    '--section-y-phone': '48px',
    '--container-max': '1120px',
    '--container-gutter-desktop': '32px',
    '--container-gutter-tablet': '24px',
    '--container-gutter-phone': '16px',
}


# 角色启发式映射规约（移植 `ROLE_HINTS`）：name → (needles, validator)。
_ROLE_HINTS: dict[str, tuple[tuple[str, ...], Callable[[str], bool]]] = {
    '--bg': (('background', 'bg'), css.is_color_value),
    '--surface': (('surface', 'card', 'popover', 'panel'), css.is_color_value),
    '--surface-warm': (('surface-warm', 'secondary', 'subtle'), css.is_color_value),
    '--fg': (('foreground', 'text', 'fg'), css.is_color_value),
    '--fg-2': (('text-secondary', 'secondary-foreground', 'secondary-fg', 'fg-2'), css.is_color_value),
    '--muted': (('muted', 'placeholder', 'subtext'), css.is_color_value),
    '--meta': (('meta', 'caption', 'tertiary'), css.is_color_value),
    '--border': (('border',), css.is_color_value),
    '--border-soft': (('border-soft', 'border-subtle', 'separator'), css.is_color_value),
    '--accent': (('accent', 'primary', 'brand'), css.is_color_value),
    '--accent-on': (
        ('accent-on', 'primary-foreground', 'accent-foreground', 'on-primary'),
        css.is_color_value,
    ),
    '--success': (('success', 'positive'), css.is_color_value),
    '--warn': (('warning', 'warn'), css.is_color_value),
    '--danger': (('danger', 'error', 'destructive'), css.is_color_value),
    '--font-display': (
        ('font-display', 'font-heading', 'font-title', 'font-sans', 'font-family'),
        css.is_font_value,
    ),
    '--font-body': (('font-body', 'font-sans', 'font-family', 'font'), css.is_font_value),
    '--font-mono': (('font-mono', 'font-code', 'font-monospace'), css.is_font_value),
    '--radius-sm': (('radius-sm', 'radius-small'), css.is_length_like),
    '--radius-md': (('radius-md', 'radius-card', 'radius'), css.is_length_like),
    '--radius-lg': (('radius-lg', 'radius-xl', 'radius-panel'), css.is_length_like),
    '--radius-pill': (('radius-pill', 'radius-full'), css.is_length_like),
    '--elev-flat': (('elev-flat', 'shadow-none'), css.is_shadow_value),
    '--elev-ring': (('elev-ring', 'ring'), css.is_shadow_value),
    '--elev-raised': (('elev-raised', 'shadow', 'elevation'), css.is_shadow_value),
    '--focus-ring': (('focus-ring', 'focus'), css.is_shadow_value),
    '--motion-fast': (('motion-fast', 'duration-fast'), css.is_duration_value),
    '--motion-base': (('motion-base', 'duration-base', 'duration'), css.is_duration_value),
    '--ease-standard': (('ease-standard', 'ease', 'easing'), css.is_easing_value),
    '--container-max': (('container-max', 'container'), css.is_length_like),
    '--container-gutter-desktop': (
        ('container-gutter-desktop', 'gutter-desktop'),
        css.is_length_like,
    ),
    '--container-gutter-tablet': (('container-gutter-tablet', 'gutter-tablet'), css.is_length_like),
    '--container-gutter-phone': (('container-gutter-phone', 'gutter-phone'), css.is_length_like),
}


def _value_is_usable_for_schema(value: str) -> bool:
    return all(ref in SCHEMA_NAMES for ref in css.extract_var_references(value))


def _source_refs(token: SourceToken) -> list[str]:
    primary = token.source if token.line is None else f'{token.source}:{token.line}'
    seen: set[str] = set()
    out: list[str] = []
    for reference in [primary, *token.usage]:
        if reference not in seen:
            seen.add(reference)
            out.append(reference)
    return out


def _strip_prefix(value: str, prefix: str) -> str:
    return value.removeprefix(prefix)


def _score_token_name(name: str, needles: tuple[str, ...]) -> int:
    normalized = _strip_prefix(name.lower(), '--')
    best = 0
    for needle in needles:
        needle_norm = _strip_prefix(needle.lower(), '--')
        if normalized == needle_norm:
            best = max(best, 100)
        elif normalized.endswith(f'-{needle_norm}'):
            best = max(best, 80)
        elif needle_norm in normalized:
            best = max(best, 40)
    return best


def _best_candidate(
    tokens: list[SourceToken],
    needles: tuple[str, ...],
    validator: Callable[[str], bool],
) -> SourceToken | None:
    scored: list[tuple[int, SourceToken]] = [
        (score, token)
        for score, token in ((_score_token_name(t.name, needles), t) for t in tokens)
        if score > 0 and validator(token.value) and _value_is_usable_for_schema(token.value)
    ]
    # score 降序，平手按 name 升序（与 open-design `b.score - a.score || a.name.localeCompare`）。
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return scored[0][1] if scored else None


def _make_binding(
    spec: TokenSpec,
    value: str,
    confidence: str,
    reason: str,
    sources: list[str],
    source_name: str | None,
) -> Binding:
    return Binding(
        name=spec.name,
        layer=spec.layer,
        value=value,
        confidence=confidence,
        reason=reason,
        sources=sources,
        source_name=source_name,
    )


def _bind_schema_token(spec: TokenSpec, source_tokens: list[SourceToken]) -> Binding:
    exact = next(
        (t for t in source_tokens if t.name == spec.name and _value_is_usable_for_schema(t.value)),
        None,
    )
    if exact is not None:
        return _make_binding(
            spec,
            exact.value,
            CONF_HIGH,
            f'Exact source token {exact.name} matched TOKEN_SCHEMA.',
            _source_refs(exact),
            exact.name,
        )

    hint = _ROLE_HINTS.get(spec.name)
    if hint is not None:
        needles, validator = hint
        candidate = _best_candidate(source_tokens, needles, validator)
        if candidate is not None:
            return _make_binding(
                spec,
                candidate.value,
                CONF_MEDIUM,
                f'Mapped source token {candidate.name} to {spec.name} by role/name heuristic.',
                _source_refs(candidate),
                candidate.name,
            )

    if spec.layer == LAYER_B_SLOT and spec.alias_to is not None:
        return _make_binding(
            spec,
            spec.alias_to,
            CONF_ALIAS,
            f'No richer source tier found; using schema alias {spec.alias_to}.',
            [],
            None,
        )

    fallback = spec.fallback if spec.fallback is not None else _default_token_value(spec.name)
    if fallback is not None:
        if spec.layer == LAYER_A2:
            confidence = CONF_FALLBACK
            reason = 'No source-backed value found; using TOKEN_SCHEMA A2 fallback.'
        else:
            confidence = CONF_LOW
            reason = 'No source-backed A1 value found; using conservative importer default.'
        return _make_binding(spec, fallback, confidence, reason, [], None)

    return _make_binding(
        spec,
        'initial',
        CONF_LOW,
        'No source-backed value or schema fallback found.',
        [],
        None,
    )


def render_contract_css(bindings: list[Binding]) -> str:
    """渲染标准 tokens.css（``:root { ... }``，绑定逐行）。"""
    lines = [
        ':root {',
        '  /* hasn TOKEN_SCHEMA contract. Evidence lives in source/token-contract.report.json. */',
    ]
    lines.extend(f'  {binding.name}: {binding.value};' for binding in bindings)
    lines.extend(('}', ''))
    return '\n'.join(lines)


def _strip_plain_root_blocks(source: str) -> str:
    """删除每个 ``:root(?!\\[)\\s*{ ... }``（到首个 ``}``），用于评审反模式统计。"""
    out: list[str] = []
    rest = source
    while True:
        rel = rest.find(':root')
        if rel == -1:
            out.append(rest)
            break
        after = rel + len(':root')
        if rest[after:].startswith('['):
            out.append(rest[:after])
            rest = rest[after:]
            continue
        j = after
        n = len(rest)
        while j < n and rest[j] in css._ASCII_WS:
            j += 1
        if j < n and rest[j] == '{':
            close_rel = rest.find('}', j + 1)
            if close_rel != -1:
                out.append(rest[:rel])
                rest = rest[close_rel + 1 :]
                continue
        out.append(rest[:after])
        rest = rest[after:]
    return ''.join(out)


def validate_token_outputs(
    tokens_css: str,
    fixture_html: str | None,
    allowed_extensions: list[str],
) -> dict[str, Any]:
    """四层契约自检：缺 token / 非 schema token / 引用未声明 / fixture 反模式。

    返回 ``{ok, errors, warnings}``（与 Rust `SelfCheck` serde 一致，字段无 rename）。
    """
    warnings: list[str] = []
    declarations = css.parse_token_declarations(tokens_css)
    declared_names = {name for name, _ in declarations}

    errors: list[str] = [
        f'tokens.css is missing {spec.name}' for spec in TOKEN_SCHEMA if spec.name not in declared_names
    ]
    for name, _ in declarations:
        if name not in SCHEMA_NAMES and not is_allowed_extension(name, allowed_extensions):
            errors.append(f'tokens.css declares non-schema token {name}')
    for name, value in declarations:
        errors.extend(
            f'{name} references undeclared token {reference}'
            for reference in css.extract_var_references(value)
            if reference not in declared_names
        )
    if fixture_html is not None:
        errors.extend(
            f'components.html references undeclared token {reference}'
            for reference in css.extract_var_references(fixture_html)
            if reference not in declared_names
        )
        without_root = _strip_plain_root_blocks(fixture_html)
        accent_uses = css.count_substring(without_root, 'var(--accent)')
        if accent_uses > 2:
            warnings.append(
                f'components.html references --accent {accent_uses} times; schema lint target is <=2 visible uses'
            )

    return {'ok': not errors, 'errors': errors, 'warnings': warnings}


def _is_source_backed(confidence: str) -> bool:
    return confidence in _SOURCE_BACKED


def _build_report(
    bindings: list[Binding],
    generated_at: str,
    self_check: dict[str, Any],
) -> dict[str, Any]:
    def layer_stat(layer: str) -> dict[str, int]:
        layer_bindings = [b for b in bindings if b.layer == layer]
        return {
            'total': len(layer_bindings),
            'source_backed': sum(1 for b in layer_bindings if _is_source_backed(b.confidence)),
            'fallback': sum(1 for b in layer_bindings if b.confidence in _FALLBACK_OR_LOW),
            'alias': sum(1 for b in layer_bindings if b.confidence == CONF_ALIAS),
        }

    layers = {
        'A1-identity': layer_stat('A1-identity'),
        'A1-structure': layer_stat('A1-structure'),
        'A2': layer_stat('A2'),
        'B-slot': layer_stat('B-slot'),
    }

    a1_bindings = [b for b in bindings if layer_is_a1(b.layer)]
    source_backed_a1 = sum(1 for b in a1_bindings if _is_source_backed(b.confidence))
    fallback_tokens = sum(1 for b in bindings if b.confidence in _FALLBACK_OR_LOW)
    alias_tokens = sum(1 for b in bindings if b.confidence == CONF_ALIAS)
    source_backed_tokens = sum(1 for b in bindings if _is_source_backed(b.confidence))

    a1_coverage = 1.0 if not a1_bindings else source_backed_a1 / len(a1_bindings)
    non_fallback_ratio = 1.0 if not bindings else 1.0 - fallback_tokens / len(bindings)
    non_alias_ratio = 1.0 if not bindings else 1.0 - alias_tokens / len(bindings)
    raw = (a1_coverage * 0.7 + non_fallback_ratio * 0.2 + non_alias_ratio * 0.1) * 100.0
    # Rust f64::round() = round-half-away-from-zero；raw 恒 >=0，故 floor(raw+0.5) 等价。
    score = max(0, min(100, math.floor(raw + 0.5)))
    if score >= 80:
        grade = GRADE_EXCELLENT
    elif score >= 60:
        grade = GRADE_USABLE
    elif score >= 40:
        grade = GRADE_NEEDS_REVIEW
    else:
        grade = GRADE_NEEDS_REBUILD
    recommend_rebuild = grade in (GRADE_NEEDS_REVIEW, GRADE_NEEDS_REBUILD) or not self_check['ok']

    return {
        'schemaVersion': 1,
        'contract': 'TOKEN_SCHEMA',
        'generatedAt': generated_at,
        'summary': {
            'totalTokens': len(TOKEN_SCHEMA),
            'declaredTokens': len(bindings),
            'sourceBackedTokens': source_backed_tokens,
            'sourceBackedA1': source_backed_a1,
            'requiredA1': len(a1_bindings),
            'fallbackTokens': fallback_tokens,
            'aliasTokens': alias_tokens,
            'score': score,
            'grade': grade,
            'recommendRebuild': recommend_rebuild,
        },
        'layers': layers,
        'selfCheck': self_check,
        'tokens': [binding_to_dict(b) for b in bindings],
    }


def binding_to_dict(binding: Binding) -> dict[str, Any]:
    """Binding → camelCase dict（字段序对齐 Rust serde；``sourceName`` skip-if-none）。"""
    data: dict[str, Any] = {
        'name': binding.name,
        'layer': binding.layer,
        'value': binding.value,
        'confidence': binding.confidence,
        'reason': binding.reason,
        'sources': list(binding.sources),
    }
    if binding.source_name is not None:
        data['sourceName'] = binding.source_name
    return data


@dataclass(frozen=True)
class DesignSystemContract:
    """:func:`compile` 的产物：绑定 + 报告 + 渲染的标准 tokens.css。"""

    bindings: list[Binding]
    report: dict[str, Any]
    tokens_css: str


def compile_tokens(source_tokens: list[SourceToken], generated_at: str) -> DesignSystemContract:
    """把一组 source token 编译成标准设计系统契约（绑定 + 渲染 tokens.css + 评分报告）。

    对齐 Rust `compile`（Python 避开与内置 :func:`compile` 撞名，改名 ``compile_tokens``）。
    """
    normalized = [
        SourceToken(
            name=t.name.strip(),
            value=t.value.strip(),
            source=t.source,
            line=t.line,
            usage=list(t.usage),
        )
        for t in source_tokens
    ]
    bindings = [_bind_schema_token(spec, normalized) for spec in TOKEN_SCHEMA]
    tokens_css = render_contract_css(bindings)
    self_check = validate_token_outputs(tokens_css, None, [])
    report = _build_report(bindings, generated_at, self_check)
    return DesignSystemContract(bindings=bindings, report=report, tokens_css=tokens_css)


def validate(
    tokens_css: str,
    generated_at: str,
    components_html: str | None = None,
    allowed_extensions: list[str] | None = None,
) -> dict[str, Any]:
    """对一份 tokens.css（+ 可选 components.html）做四层契约校验 + 评分，返回 report dict。"""
    allowed = allowed_extensions or []
    declarations = css.parse_token_declarations(tokens_css)
    source_tokens = [
        SourceToken(name=name, value=value, source='tokens.css', line=None, usage=[]) for name, value in declarations
    ]
    bindings = [_bind_schema_token(spec, source_tokens) for spec in TOKEN_SCHEMA]
    self_check = validate_token_outputs(tokens_css, components_html, allowed)
    return _build_report(bindings, generated_at, self_check)
