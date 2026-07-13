"""组件画廊「按需投影」纯函数测试（DSGET·取数瘦身）。

覆盖两把刀：
- summarize_gallery：多场景 HTML → 轻量摘要（场景/件数/完整度，不含 HTML）；
- slice_gallery_scene：按 data-ds-scene 切到单场景 + 随身携带 <style>；未知/无容器诚实回退整包。
"""

from __future__ import annotations

from backend.app.hasn_designsystem.core.gallery_projection import (
    slice_gallery_scene,
    summarize_gallery,
)

# 多场景画廊（类规则式·两场景各配齐 5 件必须组件）。
_MULTI_SCENE = (
    '<!DOCTYPE html><html><head><style>'
    ':root{--bg:#fff;--accent:#2563eb}'
    '.btn{background:var(--accent);color:#fff}'
    '.card{background:#fff;border:1px solid #eee}'
    '.hero{padding:48px}'
    '</style></head><body>'
    '<section data-ds-scene="brand_website">'
    '<nav data-ds-component="nav" class="nav">品牌导航</nav>'
    '<div data-ds-component="hero" class="hero">品牌 Hero</div>'
    '<div data-ds-component="features" class="card">特性区</div>'
    '<button data-ds-component="cta" class="btn">立即体验</button>'
    '<footer data-ds-component="footer">页脚</footer>'
    '</section>'
    '<section data-ds-scene="deck">'
    '<div data-ds-component="cover">封面页</div>'
    '<div data-ds-component="section">章节分隔</div>'
    '<div data-ds-component="bullets">要点页</div>'
    '<div data-ds-component="chart">图表页</div>'
    '<div data-ds-component="closing">结束页</div>'
    '</section>'
    '</body></html>'
)


def test_summarize_gallery_lists_scenes_without_html() -> None:
    """多场景 → 每场景件数/完整度 + 总件数；输出里不含任何 HTML 标签。"""
    summary = summarize_gallery(_MULTI_SCENE)
    by_id = {s['id']: s for s in summary['scenes']}
    assert set(by_id) == {'brand_website', 'deck'}
    assert by_id['brand_website']['component_count'] == 5
    assert by_id['brand_website']['complete'] is True
    assert by_id['deck']['component_count'] == 5
    assert by_id['deck']['label'] == '演示文稿'
    assert summary['total_components'] == 10
    # 摘要绝不夹带 HTML（这正是瘦身的意义）。
    assert '<section' not in repr(summary)
    assert '<div' not in repr(summary)


def test_summarize_gallery_empty_is_zero() -> None:
    """空 / 无画廊 → 零摘要，不臆造。"""
    assert summarize_gallery(None) == {'scenes': [], 'total_components': 0}
    assert summarize_gallery('') == {'scenes': [], 'total_components': 0}
    assert summarize_gallery('   ') == {'scenes': [], 'total_components': 0}


def test_slice_scene_keeps_only_target_scene_and_carries_styles() -> None:
    """切到 deck：只留 deck 场景 markup + 全量 <style>；丢弃 brand_website markup。"""
    sliced, applied = slice_gallery_scene(_MULTI_SCENE, 'deck')
    assert applied is True
    # 目标场景在。
    assert 'data-ds-scene="deck"' in sliced
    assert '封面页' in sliced and '结束页' in sliced
    # 其它场景被丢弃（token 大头）。
    assert 'data-ds-scene="brand_website"' not in sliced
    assert '品牌导航' not in sliced
    # 样式随身携带，切片仍自包含可渲染。
    assert '.btn{background:var(--accent)' in sliced
    assert ':root{--bg:#fff' in sliced


def test_slice_unknown_scene_falls_back_to_full() -> None:
    """未知场景 → 诚实回退整包（不给空画廊）。"""
    sliced, applied = slice_gallery_scene(_MULTI_SCENE, 'nonsense_scene')
    assert applied is False
    assert sliced == _MULTI_SCENE


def test_slice_scene_without_section_container_falls_back_to_full() -> None:
    """场景仅经 scene.component 归属、无 <section data-ds-scene> 容器 → 回退整包（切不动不硬切）。"""
    html = (
        '<html><head><style>:root{}</style></head><body>'
        '<div data-ds-component="brand_website.hero">Hero</div>'
        '</body></html>'
    )
    sliced, applied = slice_gallery_scene(html, 'brand_website')
    assert applied is False
    assert sliced == html


def test_slice_scene_balances_nested_sections() -> None:
    """场景容器内嵌 <section> → 平衡匹配收整个外层容器（含内嵌段），不在内层 </section> 早停。"""
    html = (
        '<html><head><style>.x{color:#000}</style></head><body>'
        '<section data-ds-scene="deck">'
        '<div data-ds-component="cover">封面</div>'
        '<section class="inner-group"><div data-ds-component="bullets">要点</div></section>'
        '</section>'
        '<section data-ds-scene="poster">'
        '<div data-ds-component="hero_poster">主视觉</div>'
        '</section>'
        '</body></html>'
    )
    sliced, applied = slice_gallery_scene(html, 'deck')
    assert applied is True
    # 外层 deck 容器整段（含内嵌 inner-group + 两件组件）都在。
    assert '封面' in sliced and '要点' in sliced
    assert 'inner-group' in sliced
    # 没把 poster 场景带进来。
    assert 'data-ds-scene="poster"' not in sliced
    assert '主视觉' not in sliced
