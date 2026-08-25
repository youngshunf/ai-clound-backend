"""组件画廊「按场景写入」纯函数测试（DSPUT·分片写入）。

覆盖：
- split/compose 往返稳定（拆-装-再拆 得到同样的分解）；
- upsert_scene 只动目标场景，**其余场景原样保留**（这是分片相对整包替换的全部价值）；
- 宽容输入三形态（完整文档 / 裸 section / 无包裹片段）；
- 传错场景如实抛错（不静默把 A 的内容盖到 B 头上）；
- 组装结果仍能被 detect_scenes / assess_gallery_health 正确处理（三处对整包与切片同构）。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_designsystem.core.gallery_compose import (
    GalleryParts,
    compose_gallery,
    normalize_scene_input,
    remove_scene,
    split_gallery,
    upsert_scene,
)
from backend.app.hasn_designsystem.core.gallery_health import assess_gallery_health
from backend.app.hasn_designsystem.core.gallery_projection import slice_gallery_scene, summarize_gallery
from backend.app.hasn_designsystem.core.scenes import detect_scenes

_STYLE = (
    '<style>:root{--bg:#fff;--accent:#2563eb}'
    '.btn{background:var(--accent);color:#fff}'
    '.card{background:#fff;border:1px solid #eee}'
    '.hero{padding:48px}</style>'
)
_BRAND_SECTION = (
    '<section data-ds-scene="brand_website">'
    '<nav data-ds-component="nav" class="nav">品牌导航</nav>'
    '<div data-ds-component="hero" class="hero">品牌 Hero</div>'
    '<div data-ds-component="features" class="card">特性区</div>'
    '<button data-ds-component="cta" class="btn">立即体验</button>'
    '<footer data-ds-component="footer">页脚</footer>'
    '</section>'
)
_DECK_SECTION = (
    '<section data-ds-scene="deck">'
    '<div data-ds-component="cover" class="hero">封面页</div>'
    '<div data-ds-component="section" class="card">章节分隔</div>'
    '<div data-ds-component="bullets" class="card">要点页</div>'
    '<div data-ds-component="chart" class="card">图表页</div>'
    '<div data-ds-component="closing" class="btn">结束页</div>'
    '</section>'
)
_MULTI = f'<!DOCTYPE html><html><head>{_STYLE}</head><body>{_BRAND_SECTION}{_DECK_SECTION}</body></html>'


def test_split_separates_styles_scenes_and_loose() -> None:
    parts = split_gallery(_MULTI)
    assert list(parts.scenes) == ['brand_website', 'deck']  # 按文档序
    assert '.btn{' in parts.styles
    assert '<style' not in parts.scenes['brand_website']  # 样式不重复进场景块
    assert 'data-ds-component="hero"' in parts.scenes['brand_website']
    assert 'data-ds-component="cover"' in parts.scenes['deck']
    assert not parts.loose  # 全部 markup 都归属到场景，无游离


def test_split_compose_roundtrip_is_stable() -> None:
    """拆-装-再拆必须得到同样的分解——否则每写一次场景，画廊都在悄悄变形。"""
    first = split_gallery(_MULTI)
    second = split_gallery(compose_gallery(first))
    assert second.scenes == first.scenes
    assert second.styles == first.styles
    assert second.loose == first.loose


def test_upsert_scene_leaves_other_scenes_byte_identical() -> None:
    """改 deck → brand_website 那一块必须**逐字节**不变。

    整包替换时代「漏传即清空」（13/13 变 0/13 而 save 照样 200）就是从这里根治的：
    没传的场景在服务端原地不动，压根不经过分身的手。
    """
    new_deck = (
        '<section data-ds-scene="deck">'
        '<div data-ds-component="cover" class="hero">改过的封面</div>'
        '</section>'
    )
    updated = upsert_scene(_MULTI, 'deck', new_deck)
    parts = split_gallery(updated)
    assert parts.scenes['brand_website'] == split_gallery(_MULTI).scenes['brand_website']
    assert '改过的封面' in parts.scenes['deck']
    assert '要点页' not in parts.scenes['deck']  # 目标场景是整体替换，不是追加
    assert '.btn{' in parts.styles  # 未随新 markup 传样式 → 沿用既有共享样式


def test_upsert_new_scene_appends_without_touching_existing() -> None:
    poster = '<section data-ds-scene="poster"><div data-ds-component="hero_poster" class="card">主视觉</div></section>'
    updated = upsert_scene(_MULTI, 'poster', poster)
    parts = split_gallery(updated)
    assert list(parts.scenes) == ['brand_website', 'deck', 'poster']  # 追加到末尾，不打乱既有顺序
    assert '品牌 Hero' in parts.scenes['brand_website']
    assert '封面页' in parts.scenes['deck']


def test_upsert_accepts_full_document_from_get_gallery() -> None:
    """形态 1：分身把 get_gallery(scene=…) 的完整文档改完直接回传——这是最自然的往返，必须能收。"""
    sliced, applied = slice_gallery_scene(_MULTI, 'deck')
    assert applied
    edited = sliced.replace('封面页', '新封面页')
    updated = upsert_scene(_MULTI, 'deck', edited)
    parts = split_gallery(updated)
    assert '新封面页' in parts.scenes['deck']
    assert parts.scenes['brand_website'] == split_gallery(_MULTI).scenes['brand_website']
    assert '<html' not in parts.scenes['deck']  # 外壳标签被剥掉，不嵌套进另一份文档


def test_upsert_accepts_bare_fragment_and_wraps_it() -> None:
    """形态 3：没有 section 包裹的组件片段 → 自动包上场景容器（并因此能被 detect_scenes 认出）。"""
    updated = upsert_scene(_MULTI, 'poster', '<div data-ds-component="hero_poster" class="card">主视觉</div>')
    detected = {s['id'] for s in detect_scenes(updated)}
    assert 'poster' in detected
    assert 'data-ds-scene="poster"' in updated


def test_upsert_with_styles_replaces_shared_styles() -> None:
    """传入 markup 自带 <style> → 整体替换共享样式（画廊只有一份共享样式）。"""
    updated = upsert_scene(
        _MULTI,
        'deck',
        '<style>.btn{background:#f00}</style>' + _DECK_SECTION,
    )
    parts = split_gallery(updated)
    assert '.btn{background:#f00}' in parts.styles
    assert '--accent:#2563eb' not in parts.styles  # 旧的整份被换掉，不是拼接叠加


def test_wrong_scene_in_markup_raises_instead_of_silently_overwriting() -> None:
    """markup 里只有 brand_website 的容器却声明写 deck → 抛错。

    静默按「无包裹片段」处理会把品牌网站那一整块盖到 deck 名下——两个场景的内容当场串味，
    而 detect_scenes 事后只会说 deck 有 5 件，看不出串了。
    """
    with pytest.raises(ValueError, match='brand_website'):
        normalize_scene_input('deck', _BRAND_SECTION)


def test_unknown_scene_and_empty_markup_raise() -> None:
    with pytest.raises(ValueError, match='未知场景'):
        normalize_scene_input('nosuch_scene', _DECK_SECTION)
    with pytest.raises(ValueError, match='为空'):
        normalize_scene_input('deck', '   ')


def test_legacy_gallery_without_scene_markers_keeps_content_in_loose() -> None:
    """存量画廊没打场景标记 → 正文全进 loose，写新场景时不丢它。"""
    legacy = f'<html><head>{_STYLE}</head><body><div class="card">老画廊内容</div></body></html>'
    parts = split_gallery(legacy)
    assert parts.scenes == {}
    assert '老画廊内容' in parts.loose

    updated = upsert_scene(legacy, 'deck', _DECK_SECTION)
    assert '老画廊内容' in updated  # 看不懂的 markup 绝不丢弃
    assert 'data-ds-scene="deck"' in updated


def test_remove_scene_reports_whether_it_hit() -> None:
    updated, removed = remove_scene(_MULTI, 'deck')
    assert removed is True
    assert list(split_gallery(updated).scenes) == ['brand_website']

    unchanged, removed_again = remove_scene(updated, 'deck')
    assert removed_again is False  # 没删到就如实说没删到，不假装成功
    assert unchanged == updated


def test_composed_gallery_still_passes_health_and_scene_detection() -> None:
    """组装结果必须仍能过可渲染性硬闸、且场景检测口径不变——三处对整包与切片同构。"""
    updated = upsert_scene(_MULTI, 'deck', _DECK_SECTION)
    assert assess_gallery_health(updated).healthy is True
    summary = summarize_gallery(updated)
    by_id = {s['id']: s for s in summary['scenes']}
    assert by_id['brand_website']['complete'] is True
    assert by_id['deck']['complete'] is True
    assert summary['total_components'] == 10


def test_compose_empty_parts_yields_empty_string_not_a_skeleton() -> None:
    """全空不得组装出「只有骨架的空文档」——那会让完整度判定把「没有画廊」误判成「有画廊」。"""
    composed = compose_gallery(GalleryParts('', {}, ''))
    assert not composed
    assert '<html' not in composed  # 反向：不得退化成「只有骨架的空文档」
    assert split_gallery(None).scenes == {}
    assert split_gallery('') == GalleryParts('', {}, '')
