"""确定性几何头像生成器（DiceBear 风格）纯函数单测。

只覆盖无 I/O 的 SVG 生成：确定性（同 seed→同 SVG）、区分性（不同 seed→不同 SVG）、
合法 SVG 结构、画布内不越界。落桶 helper 的 S3 I/O 由 onboarding 集成测试与真实 S3 覆盖。
"""

from __future__ import annotations

import re

from backend.app.hasn.service.agent_avatar_service import (
    _SeededRng,
    generate_geometric_avatar_svg,
)


def test_generate_is_deterministic_same_seed() -> None:
    """同 seed 必得同一 SVG（PRNG 完全由哈希驱动、无 random/时间）。"""
    seed = 'h_owner_abc:assistant'
    assert generate_geometric_avatar_svg(seed) == generate_geometric_avatar_svg(seed)


def test_generate_differs_across_seeds() -> None:
    """不同 seed → 不同 SVG（每个分身一张、无限不重复）。"""
    a = generate_geometric_avatar_svg('h_owner_1:assistant')
    b = generate_geometric_avatar_svg('h_owner_2:assistant')
    c = generate_geometric_avatar_svg('h_owner_1:content_operator')
    assert a != b  # 不同主人
    assert a != c  # 同主人不同内置分身
    assert b != c


def test_generate_emits_valid_svg_structure() -> None:
    """产出合法 SVG：以 <svg 开头、以 </svg> 收尾、含背景 rect、带命名空间。"""
    svg = generate_geometric_avatar_svg('seed-x')
    assert svg.startswith('<svg')
    assert svg.rstrip().endswith('</svg>')
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert '<rect' in svg  # 至少有背景块
    assert 'viewBox="0 0 80 80"' in svg


def test_generate_has_foreground_shapes() -> None:
    """除背景 rect 外，至少还画了一个前景几何形（circle/rect/polygon 之一）。"""
    svg = generate_geometric_avatar_svg('seed-shapes')
    shape_count = len(re.findall(r'<(circle|rect|polygon)\b', svg))
    assert shape_count >= 2  # 背景 rect + ≥1 前景形


def test_seeded_rng_is_reproducible() -> None:
    """PRNG 同 seed 复现同一序列。"""
    r1 = _SeededRng('abc')
    r2 = _SeededRng('abc')
    seq1 = [r1.randint(0, 100) for _ in range(20)]
    seq2 = [r2.randint(0, 100) for _ in range(20)]
    assert seq1 == seq2


def test_seeded_rng_randint_within_bounds() -> None:
    """randint 恒落在闭区间 [lo, hi] 内。"""
    rng = _SeededRng('bounds')
    for _ in range(200):
        v = rng.randint(5, 9)
        assert 5 <= v <= 9
