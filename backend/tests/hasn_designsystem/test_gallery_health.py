"""组件画廊可渲染性体检纯函数测试（山茶茶间 561 事故根治）。

三种真实形态对照（均出自库里真数据的最小复刻）：
- 内置库范式（内联式·健康）：每个元素带 style="…var(--token)…"，零类规则；
- Astra Pitch Dark 范式（类规则式·健康）：<style> 内嵌完整 .btn{}/.card{} 组件类规则；
- 山茶茶间范式（坏）：正文用类名，<style> 只有 :root{} 变量、零类规则，元素也没内联兜底。
"""

from __future__ import annotations

from backend.app.hasn_designsystem.core.gallery_health import assess_gallery_health

# ── 内联式（内置库 287 极简墨白范式）：零类规则，但几乎每个元素内联 var(--token） → 健康 ──
_INLINE_STYLED = (
    '<!DOCTYPE html><html><head><style>:root{--bg:#fff;--fg:#111;--accent:#2563eb}</style></head><body>'
    '<section class="ds-gallery" style="background:var(--bg);color:var(--fg);padding:24px">'
    '<h1 class="ds-title" style="font-size:2rem;color:var(--fg)">标题</h1>'
    '<p class="ds-lede" style="color:var(--fg)">正文</p>'
    '<button class="btn" style="background:var(--accent);color:#fff;padding:8px 16px;border-radius:8px">主操作</button>'
    '<button class="btn" style="background:var(--accent);color:#fff;padding:8px 16px">次操作</button>'
    '<div class="card" style="background:#fff;border:1px solid #eee;padding:16px">'
    '<div class="card-title" style="font-weight:700">卡片标题</div>'
    '<div class="card-body" style="color:var(--fg)">卡片正文</div>'
    '<span class="price-tag" style="color:var(--accent)">¥28</span></div>'
    '</section></body></html>'
)

# ── 类规则式（Astra Pitch Dark 655 范式）：<style> 内嵌完整组件类规则 → 健康 ──
_CLASS_RULE_STYLED = (
    '<!DOCTYPE html><html><head><style>'
    ':root{--bg:#0b0f1a;--fg:#e8eefc;--accent:#5b8cff}'
    '.btn{background:var(--accent);color:#fff;padding:8px 16px;border-radius:8px}'
    '.btn-secondary{background:transparent;color:var(--accent)}'
    '.card{background:#141a2b;border-radius:12px;padding:16px}'
    '.card-title{font-weight:700;color:var(--fg)}'
    '.hero{padding:48px;background:var(--bg)}'
    '.nav{display:flex;gap:16px}'
    '.section-title{font-size:1.25rem;color:var(--fg)}'
    '</style></head><body>'
    '<h2 class="section-title">Buttons</h2>'
    '<button class="btn">主操作</button><button class="btn btn-secondary">次操作</button>'
    '<div class="hero"><h1>Hero</h1></div>'
    '<nav class="nav"><a href="#">首页</a><a href="#">菜单</a></nav>'
    '<div class="card"><div class="card-title">卡片</div></div>'
    '</body></html>'
)

# ── 坏（山茶茶间 561 范式）：正文重度用类名，<style> 只有 :root{}、零类规则，几乎无内联 → 不健康 ──
_ORPHAN_CLASSES = (
    '<!DOCTYPE html><html><head><style>'
    ':root{--bg:#FCFAF8;--fg:#3A332D;--accent:#D97706}'  # 仅 :root，无任何组件类规则
    '</style></head><body>'
    '<h1 class="page-title">山茶茶间 · 组件画廊</h1>'
    '<h2 class="section-title">Buttons</h2>'
    '<button class="btn btn-primary btn-large">点单</button>'
    '<button class="btn btn-primary">加入购物车</button>'
    '<button class="btn btn-secondary">查看菜单</button>'
    '<button class="btn btn-ghost">收藏</button>'
    '<div class="hero"><h2 class="hero-title">秋日限定</h2><p class="hero-sub">手作桂花酒酿</p></div>'
    '<nav class="nav"><div class="nav-logo">山茶茶间</div>'
    '<a href="#" class="nav-link nav-link-active">首页</a><a href="#" class="nav-link">菜单</a></nav>'
    '<div class="card"><div class="card-title">山茶茉莉</div>'
    '<div class="card-body">高山茉莉绿茶</div><span class="price-tag">¥28</span></div>'
    '</body></html>'
)


def test_inline_styled_gallery_is_healthy() -> None:
    """内联式：零类规则但内联足量兜底 → 健康（内置库 150 套走此形态）。"""
    health = assess_gallery_health(_INLINE_STYLED)
    assert health.healthy is True
    assert health.reason == ''
    assert health.inline_style_count >= health.class_usage_count


def test_class_rule_styled_gallery_is_healthy() -> None:
    """类规则式：<style> 有组件类规则 → 健康（Astra Pitch Dark 走此形态）。"""
    health = assess_gallery_health(_CLASS_RULE_STYLED)
    assert health.healthy is True
    assert health.rule_count > 0


def test_orphan_class_gallery_is_unhealthy() -> None:
    """坏形态：类名用满、零类规则、内联不足 → 不健康，reason 含可照做的整改指引。"""
    health = assess_gallery_health(_ORPHAN_CLASSES)
    assert health.healthy is False
    assert health.rule_count == 0
    assert health.class_usage_count >= 8
    assert health.inline_style_count < health.class_usage_count
    # 整改说明要能指引分身二选一（内联 / 类规则）。
    assert 'style="' in health.reason
    assert '<style>' in health.reason


def test_empty_or_missing_gallery_is_healthy() -> None:
    """空 / 缺画廊由完整度闸管，本闸放行不误判（避免拦掉草稿式增量保存）。"""
    assert assess_gallery_health(None).healthy is True
    assert assess_gallery_health('').healthy is True
    assert assess_gallery_health('   ').healthy is True


def test_trivial_fixture_below_threshold_is_healthy() -> None:
    """极简 fixture（类名用法 < 阈值）不触发，保护存量测试与草稿。"""
    assert assess_gallery_health('<button class="btn">Go</button>').healthy is True
    assert assess_gallery_health('<div><span>hi</span></div>').healthy is True
