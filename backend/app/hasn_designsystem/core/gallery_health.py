"""组件画廊「可渲染性」体检（纯函数·零 IO·零副作用）。

根因（山茶茶间 561 事故）：分身产出的 ``components.html`` 正文用了 CSS 类名
（``class="btn"/"card"/"hero"…``），却在 ``<style>`` 里只写了 ``:root{}`` 变量、没写
任何组件类规则，元素也没内联样式兜底 → 类名指向的样式全部不存在 → 组件画廊退化成浏览器
默认的裸语义 HTML（默认标题字号 / 无色 / 无卡片 / 无布局）。渲染侧（webui 详情页把
``tokens_css`` 注入沙箱 iframe）是好的，缺料的是**库里存的数据本身**。

两种自包含正解都算健康（同一渲染器都能正确渲染）：
- **内联式**（内置库范式）：几乎每个可视元素带 ``style="…var(--token)…"``（内联数 ≥ 类名用法）；
- **类规则式**（Astra Pitch Dark 范式）：``<style>`` 内嵌完整 ``.btn{}/.card{}`` 组件类规则。

体检口径保守（避免误杀，只拦铁定坏的）：仅当同时满足下面三条才判不健康——
  ① ``<style>`` 里零样式规则（``:root``/keyframes 之外一条都没有）；
  ② 正文重度依赖类名（class 用法 ≥ :data:`_MIN_CLASS_USAGE_FOR_GATE`）；
  ③ 内联样式远不足以兜底（内联 ``style`` 数 < class 用法数）。
只要**有任意一条样式规则**、或**内联足量**、或**几乎不用类名** → 一律放行。
"""

from __future__ import annotations

import re

from dataclasses import dataclass

from .components import extract_components

# 内联 style 属性计数（``style="…"`` / ``style='…'``；``\bstyle`` 防误配 ``data-style`` 等）。
_INLINE_STYLE_RE = re.compile(r'(?i)\bstyle\s*=\s*["\']')

# 触发闸门的 class 用法下限：低于此不判（草稿 / 极简 fixture 不误伤，如 ``<button class="btn">``）。
_MIN_CLASS_USAGE_FOR_GATE = 8


@dataclass(frozen=True)
class GalleryHealth:
    """一次组件画廊体检结果。``healthy=False`` 时 ``reason`` 给分身可照做的整改说明。"""

    healthy: bool
    reason: str
    # ``<style>`` 里 ``:root``/keyframes 之外的样式规则条数（0 = 没有任何组件类/元素规则）。
    rule_count: int
    # 正文里出现的内联 ``style=""`` 属性个数。
    inline_style_count: int
    # 正文 ``class=""`` 里用到的不同类名个数。
    class_usage_count: int


def _count_inline_styles(html: str) -> int:
    return len(_INLINE_STYLE_RE.findall(html))


def assess_gallery_health(components_html: str | None) -> GalleryHealth:
    """体检 ``components.html`` 是否「自包含可渲染」。

    见模块 docstring 的根因与判据。空 / 缺画廊由其它校验（完整度闸）管，本闸放行不误判。
    复用 :func:`extract_components` 做权威解析（不信分身自带的 manifest，零信任重算）。
    """
    if not components_html or not components_html.strip():
        return GalleryHealth(True, '', 0, 0, 0)

    manifest = extract_components('gallery-health', components_html, None)
    fixture = manifest.get('fixture', {}) if isinstance(manifest, dict) else {}
    # ``selectorCount`` 已在 extract_components 里排除 ``:root``/keyframes → 即真正的组件/元素规则数。
    rule_count = int(fixture.get('selectorCount') or 0)
    class_usage_count = int(fixture.get('classCount') or 0)
    inline_style_count = _count_inline_styles(components_html)

    unstyled = (
        rule_count == 0
        and class_usage_count >= _MIN_CLASS_USAGE_FOR_GATE
        and inline_style_count < class_usage_count
    )
    if unstyled:
        reason = (
            f'组件画廊不可渲染：正文用了 {class_usage_count} 个 CSS 类名，但 <style> 里没有任何'
            f'组件样式规则（只有 :root 变量），元素也几乎没有内联样式（仅 {inline_style_count} 处）'
            f' → 类名指向的样式全部缺失，画廊会退化成裸语义 HTML（默认标题、无色、无卡片）。'
            f'请让画廊自包含，二选一并贯彻到底：'
            f'(a) 每个可视元素直接内联 style="…var(--token)…"；或 '
            f'(b) 在顶层 <style> 写全组件类规则（.btn{{…}}/.card{{…}} 等，颜色/间距/圆角引用 var(--token)）。'
        )
        return GalleryHealth(False, reason, rule_count, inline_style_count, class_usage_count)
    return GalleryHealth(True, '', rule_count, inline_style_count, class_usage_count)
