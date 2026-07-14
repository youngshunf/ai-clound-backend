"""默认/内置分身头像生成器（DiceBear bottts）纯函数单测。

只覆盖无 I/O 的 SVG 生成：确定性（同 seed→同 SVG）、区分性（不同 seed→不同 SVG）、
合法 SVG 结构（bottts 机器人风格）。落桶 helper 的 S3 I/O 由 onboarding 集成测试与真实 S3 覆盖。
"""

from __future__ import annotations

from backend.app.hasn.service.agent_avatar_service import generate_bottts_avatar_svg


def test_generate_is_deterministic_same_seed() -> None:
    """同 seed 必得同一 SVG（DiceBear seed 驱动内部 PRNG、无 random/时间）。"""
    seed = 'h_owner_abc:assistant'
    assert generate_bottts_avatar_svg(seed) == generate_bottts_avatar_svg(seed)


def test_generate_differs_across_seeds() -> None:
    """不同 seed → 不同 SVG（每个分身一张、无限不重复）。"""
    a = generate_bottts_avatar_svg('h_owner_1:assistant')
    b = generate_bottts_avatar_svg('h_owner_2:assistant')
    c = generate_bottts_avatar_svg('h_owner_1:content_operator')
    assert a != b  # 不同主人
    assert a != c  # 同主人不同内置分身
    assert b != c


def test_generate_emits_valid_svg_structure() -> None:
    """产出合法 SVG：以 <svg 开头、以 </svg> 收尾、带命名空间、bottts 画布 viewBox。"""
    svg = generate_bottts_avatar_svg('seed-x')
    assert svg.startswith('<svg')
    assert svg.rstrip().endswith('</svg>')
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    # DiceBear bottts 画布为 180x180
    assert 'viewBox="0 0 180 180"' in svg


def test_generate_is_bottts_style() -> None:
    """确系 DiceBear 产出（带 DiceBear 生成标记），且是非空的机器人图形。"""
    svg = generate_bottts_avatar_svg('seed-shapes')
    assert 'dicebear' in svg.lower()  # DiceBear 生成标记
    assert len(svg) > 500  # 机器人图形有实体内容，非空壳
