"""设计系统 core 纯函数单测（Python 移植 hasn-designsystem-core，语义对齐 Rust）。

覆盖：schema 完整性 / css 校验器与解析原语 / compile_tokens 四类置信度分支 /
validate 自检四类错误 + 评分公式（Rust round-half-away 定值）/ derive 形状 /
extract_components 形状（含 pixelValues 忠实非回溯行为）。全部离线纯函数，无需 DB。
"""

from __future__ import annotations

import json

from collections import Counter

from backend.app.hasn_designsystem.core import (
    COMPONENTS_MANIFEST_SCHEMA_VERSION,
    TOKEN_SCHEMA,
    DesignSystemContract,
    SourceToken,
    compile_tokens,
    css,
    derive,
    extract_components,
    infer_design_token_type,
    is_allowed_extension,
    validate,
)

TS = '2026-07-04T00:00:00+00:00'


def _binding(contract: DesignSystemContract, name: str) -> dict:
    return next(t for t in contract.report['tokens'] if t['name'] == name)


def _full_source() -> list[SourceToken]:
    """每个 schema token 一条精确同名来源（值可用无 var 引用）→ compile 后全 high。"""
    return [
        SourceToken(name=spec.name, value='#abcdef', source='seed.css', line=i + 1)
        for i, spec in enumerate(TOKEN_SCHEMA)
    ]


# ─────────────────────────── schema ───────────────────────────


def test_token_schema_has_56_entries() -> None:
    assert len(TOKEN_SCHEMA) == 56


def test_token_schema_layer_breakdown() -> None:
    counts = Counter(spec.layer for spec in TOKEN_SCHEMA)
    assert counts['A1-identity'] == 8
    assert counts['A1-structure'] == 18
    assert counts['A2'] == 26
    assert counts['B-slot'] == 4


def test_a2_have_fallback_b_slots_have_alias() -> None:
    for spec in TOKEN_SCHEMA:
        if spec.layer == 'A2':
            assert spec.fallback is not None, spec.name
        if spec.layer == 'B-slot':
            assert spec.alias_to is not None and spec.alias_to.startswith('var('), spec.name


def test_is_allowed_extension() -> None:
    assert is_allowed_extension('--tag-bg-red', [])  # 通用前缀族
    assert not is_allowed_extension('--custom', [])
    assert is_allowed_extension('--custom', ['--custom'])  # per-brand allowlist


# ─────────────────────── css 值校验器 ───────────────────────


def test_is_color_value() -> None:
    assert css.is_color_value('#fff')
    assert css.is_color_value('#ffffff')
    assert css.is_color_value('rgb(0,0,0)')
    assert css.is_color_value('RGBA(0,0,0,1)')  # 大小写不敏感
    assert css.is_color_value('oklch(0.5 0.1 20)')
    assert css.is_color_value('color-mix(in oklab, red, blue)')
    assert css.is_color_value('var(--accent)')
    assert not css.is_color_value('#ff')  # 仅 2 位 hex
    assert not css.is_color_value('12px')
    assert not css.is_color_value('red')  # 命名色不匹配


def test_is_length_like() -> None:
    assert css.is_length_like('12px')
    assert css.is_length_like('1.5rem')
    assert css.is_length_like('100%')
    assert css.is_length_like('clamp(1rem, 2vw, 3rem)')
    assert css.is_length_like('calc(100% - 20px)')
    assert css.is_length_like('var(--x)')
    assert not css.is_length_like('.5px')  # 需至少一位整数
    assert not css.is_length_like('12')  # 无单位
    assert not css.is_length_like('bold')


def test_is_duration_value() -> None:
    assert css.is_duration_value('150ms')
    assert css.is_duration_value('0.2s')
    assert css.is_duration_value('1s')
    assert css.is_duration_value('var(--x)')
    assert not css.is_duration_value('12px')
    assert not css.is_duration_value('fast')


def test_is_easing_value() -> None:
    assert css.is_easing_value('cubic-bezier(0.2,0,0,1)')
    assert css.is_easing_value('linear')
    assert css.is_easing_value('ease')
    assert css.is_easing_value('ease-in-out')
    assert css.is_easing_value('var(--x)')
    assert not css.is_easing_value('12px')


def test_is_shadow_value() -> None:
    assert css.is_shadow_value('none')
    assert css.is_shadow_value('0 2px 8px rgba(0,0,0,.1)')
    assert css.is_shadow_value('var(--x)')
    assert css.is_shadow_value('color-mix(in oklab, red, blue)')
    assert css.is_shadow_value('0 0 0 1px red')
    assert not css.is_shadow_value('flat')  # 无数字/空格/none/var/color-mix


def test_is_font_value() -> None:
    assert css.is_font_value('Inter, sans-serif')
    assert not css.is_font_value('123')  # 无字母
    assert not css.is_font_value('x' * 181)  # 超长


# ─────────────────────── css 解析原语 ───────────────────────


def test_parse_token_declarations_dedup_keeps_order_last_value() -> None:
    decls = css.parse_token_declarations(':root{ --a: 1px; --b: 2px; --a: 9px; }')
    assert decls == [('--a', '9px'), ('--b', '2px')]


def test_extract_first_root_body_skips_attr_root() -> None:
    # :root[data-theme] 不得匹配；紧随的裸 :root 才是提取目标。
    decls = css.parse_token_declarations(':root[data-theme=dark]{ --x: 1; } :root { --a: 2px; }')
    assert decls == [('--a', '2px')]


def test_extract_var_references() -> None:
    assert css.extract_var_references('var(--accent)') == ['--accent']
    assert css.extract_var_references('color-mix(in oklab, var(--a), var(--b))') == [
        '--a',
        '--b',
    ]
    assert css.extract_var_references('#fff') == []


def test_strip_css_comments_and_collapse_whitespace() -> None:
    assert css.strip_css_comments('a /* x */ b') == 'a  b'
    assert css.collapse_whitespace('a   b\n c') == 'a b c'


def test_count_substring_non_overlapping() -> None:
    assert css.count_substring('var(--accent) var(--accent)', 'var(--accent)') == 2


# ─────────────────────── compile_tokens 分支 ───────────────────────


def test_exact_match_high() -> None:
    contract = compile_tokens([SourceToken(name='--accent', value='#2563eb', source='in.css', line=1)], TS)
    accent = _binding(contract, '--accent')
    assert accent['confidence'] == 'high'
    assert accent['value'] == '#2563eb'
    assert accent['sourceName'] == '--accent'


def test_role_hint_medium() -> None:
    # --background 非 schema 名，但按角色启发式 needle 'background' + 颜色校验器映射到 --bg。
    contract = compile_tokens([SourceToken(name='--background', value='#ffffff', source='in.css', line=1)], TS)
    bg = _binding(contract, '--bg')
    assert bg['confidence'] == 'medium'
    assert bg['value'] == '#ffffff'
    assert bg['sourceName'] == '--background'


def test_role_hint_rejects_wrong_value_type() -> None:
    # --primary 角色映射到 --accent，但值 12px 非颜色 → 拒绝，落回 A1 保守默认 low。
    contract = compile_tokens([SourceToken(name='--primary', value='12px', source='in.css', line=1)], TS)
    accent = _binding(contract, '--accent')
    assert accent['confidence'] == 'low'
    assert accent['value'] == '#2563eb'  # 导入器默认
    assert 'sourceName' not in accent


def test_b_slot_alias_when_no_source() -> None:
    contract = compile_tokens([], TS)
    surface_warm = _binding(contract, '--surface-warm')
    assert surface_warm['confidence'] == 'alias'
    assert surface_warm['value'] == 'var(--surface)'
    assert 'sourceName' not in surface_warm


def test_a2_fallback_when_no_source() -> None:
    contract = compile_tokens([], TS)
    space1 = _binding(contract, '--space-1')
    assert space1['confidence'] == 'fallback'
    assert space1['value'] == '4px'


def test_a1_identity_default_low() -> None:
    contract = compile_tokens([], TS)
    bg = _binding(contract, '--bg')
    assert bg['confidence'] == 'low'
    assert bg['value'] == '#f8fafc'  # 保守默认


def test_a1_structure_uses_conservative_default_low() -> None:
    # 现行 schema 下每个 A1 token 都有保守默认（含 A1-structure），故取默认值 + low；
    # 'initial' 分支是防御性兜底（无默认且非 A2/B-slot 才走），当前 schema 不触发。
    contract = compile_tokens([], TS)
    text_xs = _binding(contract, '--text-xs')
    assert text_xs['confidence'] == 'low'
    assert text_xs['value'] == '0.75rem'  # 导入器保守默认


def test_tokens_css_renders_every_schema_token() -> None:
    contract = compile_tokens([], TS)
    assert contract.tokens_css.startswith(':root {')
    for spec in TOKEN_SCHEMA:
        assert f'  {spec.name}: ' in contract.tokens_css


def test_report_shape_and_layerstat_keys() -> None:
    report = compile_tokens([], TS).report
    assert report['schemaVersion'] == 1
    assert report['contract'] == 'TOKEN_SCHEMA'
    assert report['generatedAt'] == TS
    assert set(report['layers'].keys()) == {'A1-identity', 'A1-structure', 'A2', 'B-slot'}
    # LayerStat 内部键 snake_case（无 serde rename）。
    assert set(report['layers']['A2'].keys()) == {'total', 'source_backed', 'fallback', 'alias'}
    assert report['layers']['B-slot']['alias'] == 4
    assert report['summary']['totalTokens'] == 56
    assert report['summary']['requiredA1'] == 26
    assert 'selfCheck' in report


# ─────────────────────── validate 自检 + 评分 ───────────────────────


def test_full_contract_scores_100_excellent() -> None:
    contract = compile_tokens(_full_source(), TS)
    report = validate(contract.tokens_css, TS)
    assert report['summary']['score'] == 100
    assert report['summary']['grade'] == 'excellent'
    assert report['selfCheck']['ok'] is True
    assert report['selfCheck']['errors'] == []


def test_missing_token_error() -> None:
    report = validate(':root{ --bg: #fff; }', TS)
    assert any('missing --accent' in e for e in report['selfCheck']['errors'])
    assert report['selfCheck']['ok'] is False


def test_non_schema_token_error() -> None:
    contract = compile_tokens(_full_source(), TS)
    bad_css = contract.tokens_css.replace('}', '  --bogus: 1px;\n}', 1)
    report = validate(bad_css, TS)
    assert any('non-schema token --bogus' in e for e in report['selfCheck']['errors'])


def test_allowed_extension_prefix_no_error() -> None:
    contract = compile_tokens(_full_source(), TS)
    ext_css = contract.tokens_css.replace('}', '  --tag-bg-red: #f00;\n}', 1)
    report = validate(ext_css, TS)
    assert not any('non-schema token' in e for e in report['selfCheck']['errors'])


def test_undeclared_reference_error() -> None:
    report = validate(':root{ --bg: var(--nope); }', TS)
    assert any('references undeclared token --nope' in e for e in report['selfCheck']['errors'])


def test_accent_overuse_warning() -> None:
    contract = compile_tokens(_full_source(), TS)
    html = '<a style="color: var(--accent)">x</a>' * 3
    report = validate(contract.tokens_css, TS, components_html=html)
    assert any('--accent 3 times' in w for w in report['selfCheck']['warnings'])


def test_score_formula_round_half_away() -> None:
    # 精确锁定评分公式 + Rust round-half-away（floor(raw+0.5)）：
    # 仅 --bg 有源 → a1cov=1/26, fallback=51/56, alias=4/56 → raw≈13.764 → 14。
    report = validate(':root{ --bg: #fff; }', TS)
    assert report['summary']['score'] == 14
    assert report['summary']['grade'] == 'needs-rebuild'


# ─────────────────────── derive ───────────────────────


def test_derive_shapes() -> None:
    contract = compile_tokens(_full_source(), TS)
    out = derive(contract.tokens_css, TS)
    dj = json.loads(out['design_tokens_json'])
    assert dj['format'] == 'hasn-design-tokens/v1'
    assert dj['schemaVersion'] == 1
    assert dj['generatedAt'] == TS
    assert len(dj['tokens']) == 56
    assert {'name', 'value', 'type', 'layer', 'confidence', 'reason', 'sources'}.issubset(dj['tokens'][0].keys())
    # 2 空格 pretty + 尾换行（对齐 serde to_string_pretty）。
    assert out['design_tokens_json'].endswith('}\n')
    assert '@theme {' in out['tailwind_v4_css']
    assert '--color-accent: var(--accent);' in out['tailwind_v4_css']


def test_derive_tailwind_omits_undeclared() -> None:
    out = derive(':root{ --bg: #fff; }', TS)
    tw = out['tailwind_v4_css']
    assert '--color-bg: var(--bg);' in tw
    assert '--color-surface:' not in tw  # --surface 未声明 → 略过映射


def test_infer_design_token_type() -> None:
    assert infer_design_token_type('--bg') == 'color'
    assert infer_design_token_type('--font-body') == 'fontFamily'
    assert infer_design_token_type('--leading-body') == 'number'
    assert infer_design_token_type('--ease-standard') == 'cubicBezier'
    assert infer_design_token_type('--motion-fast') == 'duration'
    assert infer_design_token_type('--elev-raised') == 'shadow'
    assert infer_design_token_type('--focus-ring') == 'shadow'
    assert infer_design_token_type('--text-base') == 'dimension'
    assert infer_design_token_type('--space-4') == 'dimension'


# ─────────────────────── extract_components ───────────────────────

_FIXTURE_HTML = (
    '<html><head><title>Demo &amp; Co</title>'
    '<meta name="description" content="A demo page">'
    '<style>.btn { color: var(--accent); padding: 8px; } '
    '.card { background: #fff; }</style></head>'
    '<body><button class="btn primary">Go</button>'
    '<div class="card"><svg class="icon"></svg></div></body></html>'
)


def test_manifest_shape_and_entity_decoded_title() -> None:
    contract = compile_tokens(_full_source(), TS)
    m = extract_components('demo', _FIXTURE_HTML, contract.tokens_css)
    assert m['schemaVersion'] == COMPONENTS_MANIFEST_SCHEMA_VERSION
    assert m['brandId'] == 'demo'
    assert m['fixture']['title'] == 'Demo & Co'  # &amp; 解码
    assert m['fixture']['description'] == 'A demo page'  # meta description
    assert m['selectors'] == ['.btn', '.card']
    assert m['classes'] == ['btn', 'card', 'icon', 'primary']
    assert m['source']['tokensCss'] == 'tokens.css'  # 传了 tokens_css


def test_buttons_group_detected() -> None:
    m = extract_components('demo', _FIXTURE_HTML)
    btn = next(g for g in m['groups'] if g['id'] == 'buttons')
    assert btn['present'] is True
    assert 'btn' in btn['classes']


def test_undeclared_referenced_empty_when_no_declared() -> None:
    # 未传 tokens_css 且 style 无 :root → declared 空 → undeclaredReferenced 忠实留空。
    m = extract_components('demo', _FIXTURE_HTML)
    assert m['tokens']['declared'] == []
    assert m['tokens']['referenced'] == ['--accent']
    assert m['tokens']['undeclaredReferenced'] == []


def test_literals_faithful_non_backtracking_pixel_count() -> None:
    # 忠实 Rust：整数 8px 因非回溯不计入；#fff 计一个颜色表达式。
    m = extract_components('demo', _FIXTURE_HTML)
    assert m['literals']['colorExpressions'] == 1
    assert m['literals']['pixelValues'] == 0
