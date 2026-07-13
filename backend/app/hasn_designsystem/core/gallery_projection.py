"""组件画廊「按需投影」确定性纯函数（DSGET·分身取数瘦身）。

分身经 MCP `hasn.designsystem.get` 读一套设计系统时，**默认不再回灌整包组件画廊 HTML**——
场景一多（品牌网站 / 演示文稿 / 产品海报 / 移动端，每场景一堆组件），``components_html`` 线性膨胀，
一次性塞进分身上下文既烧 token 又污染。本模块提供两把确定性纯函数：

- :func:`summarize_gallery`：从 ``components_html`` 折出**轻量场景摘要**（有哪些场景、各配齐几件、是否完整），
  **不含 HTML**——供默认 ``get`` 告诉分身「这套系统的画廊长啥样、值不值得按需取」。
- :func:`slice_gallery_scene`：按 ``data-ds-scene`` 把画廊**切到单个场景**（只留该场景的 ``<section>`` +
  **随身携带全量 ``<style>``**，保证切片自身仍是可渲染的自包含文档，对齐 :mod:`gallery_health` 的
  renderability 硬闸）——供 ``hasn.designsystem.get_gallery(scene=…)`` 只取分身当下要参考的那一场景。

标记约定与 :mod:`scenes` 完全一致：场景容器 ``<section data-ds-scene="brand_website"> … </section>``，
场景内组件 ``<div data-ds-component="hero">``。

# 纯函数约定
只看输入 HTML、无 IO、无时钟、同输入同输出。切不动 / 未知场景一律**诚实回退**（返回整包 + slice_applied=False，
绝不臆造空画廊）。
"""

from __future__ import annotations

import re

from .scenes import SCENE_STANDARDS, detect_scenes, is_known_scene

# 场景容器 <section …> 的开/闭标签（贪婪到 '>'；捕获①是否闭合标记 '/'，②标签内属性串）。
_SECTION_TAG_RE = re.compile(r"<\s*(/?)\s*section\b([^>]*)>", re.IGNORECASE)
# <style> … </style> 块（DOTALL 跨行；用于切片时随身携带样式）。
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
# 某个 <section> 开标签属性里的 data-ds-scene="值"（单双引号皆可）。
_SCENE_ATTR_RE = re.compile(r"""data-ds-scene\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

# 场景 id → 中文展示名（denorm 自单一事实源 SCENE_STANDARDS，摘要用）。
_SCENE_LABELS: dict[str, str] = {s.id: s.label for s in SCENE_STANDARDS}


def summarize_gallery(components_html: str | None) -> dict:
    """从 ``components_html`` 折出轻量画廊摘要（**不含 HTML**）。

    返回 ``{scenes: [{id, label, component_count, complete}], total_components}``：
    - ``scenes``：**实际检测到至少一件标准组件**的场景（诚实反映产出，与 :func:`scenes.detect_scenes` 同源）；
    - ``component_count``：该场景已到位的标准组件数（必须 present + 可选 present）；
    - ``complete``：该场景全部必须组件是否到位；
    - ``total_components``：所有场景已到位标准组件总数。

    空 / 无画廊 → ``{scenes: [], total_components: 0}``（不臆造）。
    """
    html = components_html.strip() if isinstance(components_html, str) else ''
    if not html:
        return {'scenes': [], 'total_components': 0}
    detected = detect_scenes(html)
    scenes: list[dict] = []
    total = 0
    for s in detected:
        count = len(s.get('presentComponents', [])) + len(s.get('optionalPresent', []))
        total += count
        scenes.append({
            'id': s['id'],
            'label': s.get('label') or _SCENE_LABELS.get(s['id'], s['id']),
            'component_count': count,
            'complete': bool(s.get('complete')),
        })
    return {'scenes': scenes, 'total_components': total}


def _collect_style_blocks(html: str) -> str:
    """收集画廊里全部 ``<style>…</style>`` 块（原样拼接）。

    切片必须随身携带样式：健康画廊要么组件类规则写在 ``<style>``（类规则式），要么元素内联
    ``style="…var(--token)…"``（内联式，样式随元素走，无需 style 块）。带上全量 ``<style>`` 对两种形态
    都安全——类规则式靠它渲染，内联式带了也无害（体积远小于被丢弃的其它场景 markup）。
    """
    return '\n'.join(_STYLE_BLOCK_RE.findall(html))


def _tag_declares_scene(attrs: str, scene: str) -> bool:
    """某个 ``<section>`` 开标签的属性串里是否声明了 ``data-ds-scene="scene"``。"""
    m = _SCENE_ATTR_RE.search(attrs)
    return m is not None and m.group(1).strip().lower() == scene


def _extract_scene_sections(html: str, scene: str) -> list[str]:
    """按文档序抽出所有归属 ``scene`` 的顶层 ``<section data-ds-scene="scene">…</section>`` 块（平衡嵌套）。

    用深度计数做平衡匹配（``<section>`` 可嵌套）：遇到声明该场景的开标签即开始捕获，记下当时深度，
    深度回落到该值时的闭标签即匹配结束。只在成对闭合时才收录（未闭合的不臆造补全）。
    """
    blocks: list[str] = []
    depth = 0
    capture_start: int | None = None
    capture_close_depth: int | None = None
    for m in _SECTION_TAG_RE.finditer(html):
        is_close = bool(m.group(1))
        if not is_close:
            # 开标签：若尚未在捕获中且此标签声明了目标场景，则起捕获（记录当前深度作为收尾深度）。
            if capture_start is None and _tag_declares_scene(m.group(2), scene):
                capture_start = m.start()
                capture_close_depth = depth
            depth += 1
        else:
            depth -= 1
            if depth < 0:  # 容忍不平衡 HTML：闭多于开时钳到 0，避免误判收尾深度
                depth = 0
            if capture_start is not None and depth == capture_close_depth:
                blocks.append(html[capture_start : m.end()])
                capture_start = None
                capture_close_depth = None
    return blocks


def slice_gallery_scene(components_html: str | None, scene: str) -> tuple[str, bool]:
    """把 ``components_html`` 切到单个 ``scene`` → ``(sliced_html, slice_applied)``。

    - 未知场景 / 空 HTML / 该场景没有 ``<section data-ds-scene>`` 容器 → **诚实回退**：原样返回整包 +
      ``slice_applied=False``（宁可多给也不给残缺/空画廊）。
    - 命中 ≥1 个场景容器 → 组一份**自包含可渲染**文档：``<style>``（全量）+ 仅该场景的 ``<section>`` 块，
      ``slice_applied=True``。丢弃的是其它场景的 markup（token 大头）。
    """
    html = components_html.strip() if isinstance(components_html, str) else ''
    scene_id = scene.strip().lower() if isinstance(scene, str) else ''
    if not html or not scene_id or not is_known_scene(scene_id):
        return html, False
    sections = _extract_scene_sections(html, scene_id)
    if not sections:
        return html, False
    styles = _collect_style_blocks(html)
    body = '\n'.join(sections)
    sliced = (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        f'{styles}\n'
        '</head>\n<body>\n'
        f'{body}\n'
        '</body>\n</html>'
    )
    return sliced, True
