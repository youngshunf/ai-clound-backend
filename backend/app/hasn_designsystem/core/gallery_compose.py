"""组件画廊「按场景写入」确定性纯函数（DSPUT·分片写入的核心）。

与 :mod:`gallery_projection`（按场景**读**切片）成对：本模块负责按场景**写**回去——把一个场景的
markup 合进整包 ``components_html``，其余场景原样保留。

# 为什么需要它
``hasn.designsystem.save`` 的 ``content`` 是**整包替换**：改一个场景也必须把全部场景的 HTML 一起重发。
实测（2026-08-25 生产分身）这条路走不通——整包画廊几十 KB，经 ``hasn.cloud.tool.call`` 再套一层 JSON 后，
分身要一次无差错吐出 2 万～4.5 万字符的双重转义串，**41% 的调用卡在「生成不出合法 JSON」**；侥幸传成功的
那些里，又有人漏传 ``components_html`` 导致整个画廊被静默清空（13/13 变 0/13，而 save 照样返回 200）。
按场景写入让单次入参降一个数量级，且**没传的场景在服务端原地不动**，从根上消掉「漏传即清空」。

# 结构约定（与 :mod:`scenes` / :mod:`gallery_projection` 完全一致）
- 场景容器：``<section data-ds-scene="brand_website"> … </section>``
- 场景内组件：``<div data-ds-component="hero">``

# 纯函数约定
只看输入、无 IO、无时钟、同输入同输出。**拆-装往返必须稳定**：``compose(split(html))`` 再 split 得到
同样的分解结果（见 ``test_gallery_compose.py`` 的往返用例）。解析不出场景时**诚实保留**原文
（进 ``loose``），绝不丢弃看不懂的 markup。
"""

from __future__ import annotations

import re

from typing import NamedTuple

# ⚠️ 复用 gallery_projection 的标签正则与平衡匹配抽取，**不另写一套**：读切片与写入必须对
# `<section>` 边界有**逐字节相同**的理解，否则会出现「切得出来、写不回去」或「写回去后切片少一块」
# 这类只在特定嵌套下暴露的分叉。同包内私有名复用是有意为之。
from .gallery_projection import (
    _SCENE_ATTR_RE,
    _SECTION_TAG_RE,
    _STYLE_BLOCK_RE,
    _extract_scene_sections,
)
from .scenes import is_known_scene

# 文档外壳标签：宽容输入时剥掉，避免把 <html>/<head>/<body> 嵌进另一份文档的 <body> 里。
_SHELL_TAG_RE = re.compile(r'(?i)</?\s*(?:!doctype|html|head|body|meta|title)\b[^>]*>')


class GalleryParts(NamedTuple):
    """一份 ``components_html`` 的确定性分解。

    - ``styles``：全部 ``<style>…</style>`` 块原样拼接（画廊共享样式，按场景写入时整体替换或保留）；
    - ``scenes``：``{场景 id: 该场景的 <section> 块原文}``，**按文档序**（dict 保序）；
      同一场景出现多个 ``<section>`` 时合并成一块（原文按序拼接），写入时整体替换；
    - ``loose``：不属于任何场景容器、也不是 ``<style>`` 的剩余 markup（存量画廊没打场景标记时全在这里）。
      **绝不丢弃**——看不懂的 markup 原样留着，由主人/分身自己决定怎么处理。
    """

    styles: str
    scenes: dict[str, str]
    loose: str


def split_gallery(components_html: str | None) -> GalleryParts:
    """把整包画廊拆成「共享样式 + 各场景 markup + 游离内容」。

    空输入 → 三者皆空。无任何场景容器（存量老画廊） → ``scenes`` 为空、正文全进 ``loose``。
    """
    html = components_html.strip() if isinstance(components_html, str) else ''
    if not html:
        return GalleryParts('', {}, '')

    styles = '\n'.join(_STYLE_BLOCK_RE.findall(html))
    body = _STYLE_BLOCK_RE.sub('', html)

    scenes: dict[str, str] = {}
    spans: list[tuple[int, int]] = []
    for scene_id in _declared_scene_ids(body):
        blocks = _extract_scene_sections(body, scene_id)
        if not blocks:
            continue
        scenes[scene_id] = '\n'.join(blocks)
        spans.extend(_span_of(body, block) for block in blocks)

    loose = _strip_spans(body, spans)
    return GalleryParts(styles, scenes, _clean_shell(loose))


def _declared_scene_ids(html: str) -> list[str]:
    """按文档序列出正文里声明过的场景 id（去重保序；未知场景 id 一并保留，不静默吞掉）。"""
    seen: list[str] = []
    for match in _SECTION_TAG_RE.finditer(html):
        if match.group(1):  # 闭标签
            continue
        attr = _SCENE_ATTR_RE.search(match.group(2))
        if attr is None:
            continue
        scene_id = attr.group(1).strip().lower()
        if scene_id and scene_id not in seen:
            seen.append(scene_id)
    return seen


def _span_of(html: str, block: str) -> tuple[int, int]:
    """定位一段场景 markup 在正文中的区间（抽取自同一份正文，必然命中）。"""
    start = html.find(block)
    return (start, start + len(block)) if start >= 0 else (-1, -1)


def _strip_spans(html: str, spans: list[tuple[int, int]]) -> str:
    """从正文里挖掉已归入场景的区间，剩下的即游离内容。"""
    kept: list[str] = []
    cursor = 0
    for start, end in sorted(s for s in spans if s[0] >= 0):
        if start >= cursor:
            kept.append(html[cursor:start])
            cursor = end
    kept.append(html[cursor:])
    return ''.join(kept)


def _clean_shell(html: str) -> str:
    """剥掉文档外壳标签并压掉纯空白（保留正文本身的换行结构）。"""
    return _SHELL_TAG_RE.sub('', html).strip()


def compose_gallery(parts: GalleryParts) -> str:
    """把分解结果组装回一份**自包含可渲染**的整包画廊。

    形状与 :func:`gallery_projection.slice_gallery_scene` 的输出一致（``<style>`` 进 ``<head>``、
    场景 ``<section>`` 按序进 ``<body>``），这样详情页沙箱 iframe、``detect_scenes``、
    ``assess_gallery_health`` 三处对整包与切片的处理完全同构。

    全空 → 返回空串（不造一份只有骨架的空文档，否则完整度判定会把「没有画廊」误判成「有画廊」）。
    """
    body_blocks = [block for block in [parts.loose, *parts.scenes.values()] if block and block.strip()]
    if not parts.styles.strip() and not body_blocks:
        return ''
    body = '\n'.join(body_blocks)
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        f'{parts.styles}\n'
        '</head>\n<body>\n'
        f'{body}\n'
        '</body>\n</html>'
    )


class SceneInput(NamedTuple):
    """一次场景写入的规整结果。``styles`` 为空表示本次不改共享样式。"""

    section_html: str
    styles: str


def normalize_scene_input(scene: str, html: str) -> SceneInput:
    """把分身传来的场景 markup 规整成「一个 ``<section data-ds-scene>`` 块 + 可选共享样式」。

    宽容接收三种形态——分身从 ``get_gallery(scene=…)`` 拿到的是**带 ``<style>`` 的完整文档**，
    改完直接回传是最自然的动作，契约若只认裸 ``<section>`` 就会把这条主路径判死：

    1. 完整文档（``<!DOCTYPE>`` + ``<style>`` + ``<section data-ds-scene>``）→ 抽出该场景 section，
       ``<style>`` 作为共享样式一并返回；
    2. 裸 ``<section data-ds-scene="…">…</section>``；
    3. 无 section 包裹的组件片段 → 自动包上 ``<section data-ds-scene="{scene}">``。

    只有一种情况报错：正文里有场景容器，但**没有一个是本次声明的 scene**——那是传错场景（或 scene
    写错），静默按形态 3 包起来会把 A 场景的内容盖到 B 场景头上。零 fake，如实抛。
    """
    scene_id = scene.strip().lower() if isinstance(scene, str) else ''
    if not scene_id or not is_known_scene(scene_id):
        raise ValueError(f'未知场景 id: {scene!r}（可用：brand_website / deck / poster / mobile）')
    raw = html.strip() if isinstance(html, str) else ''
    if not raw:
        raise ValueError(f'场景 {scene_id} 的 markup 为空——要移除该场景请用 remove_scene，不要传空串')

    styles = '\n'.join(_STYLE_BLOCK_RE.findall(raw))
    body = _clean_shell(_STYLE_BLOCK_RE.sub('', raw))

    sections = _extract_scene_sections(body, scene_id)
    if sections:
        return SceneInput('\n'.join(sections), styles)

    declared = _declared_scene_ids(body)
    if declared:
        raise ValueError(
            f'传入的 markup 里只有场景 {declared} 的容器，没有 {scene_id} 的——'
            f'请确认 scene 入参与 markup 里的 data-ds-scene 一致（要一次写多个场景请分多次调用）'
        )
    return SceneInput(f'<section data-ds-scene="{scene_id}">\n{body}\n</section>', styles)


def upsert_scene(components_html: str | None, scene: str, html: str) -> str:
    """把一个场景写进整包画廊：已存在则整体替换，不存在则按文档序追加到末尾。

    **其余场景原样保留**——这正是分片写入相对整包替换的全部价值。传入 markup 自带 ``<style>`` 时
    整体替换共享样式（画廊只有一份共享样式，切片读回来的也是全量）；不带则沿用既有样式。
    """
    parts = split_gallery(components_html)
    normalized = normalize_scene_input(scene, html)
    scenes = dict(parts.scenes)
    scenes[scene.strip().lower()] = normalized.section_html
    styles = normalized.styles if normalized.styles.strip() else parts.styles
    return compose_gallery(GalleryParts(styles, scenes, parts.loose))


def remove_scene(components_html: str | None, scene: str) -> tuple[str, bool]:
    """从整包画廊里删掉一个场景 → ``(新画廊, 是否真的删到了)``。

    没删到时如实返回 ``False``（不假装成功）——调用方据此告诉分身「这个场景本来就不在」。
    """
    scene_id = scene.strip().lower() if isinstance(scene, str) else ''
    parts = split_gallery(components_html)
    if scene_id not in parts.scenes:
        return components_html or '', False
    scenes = {k: v for k, v in parts.scenes.items() if k != scene_id}
    return compose_gallery(GalleryParts(parts.styles, scenes, parts.loose)), True
