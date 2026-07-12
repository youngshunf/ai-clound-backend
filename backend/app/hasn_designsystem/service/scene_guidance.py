"""组件画廊「场景完整度自查」报告（DSGAL 自查工具 `hasn.designsystem.check_scenes` 的业务核心）。

# 为什么要有这个模块（根因）
分身产出/精修设计系统后，常把 owner 勾选的 ``required_scenes``（「要求覆盖哪些场景」的**声明**）误当成
「场景已配齐」，从而对主人谎报「全套齐全」。真实覆盖度 = ``required_scenes`` × ``components.html`` 里
``data-ds-scene`` / ``data-ds-component`` 标记**实际检测到**的标准组件 的交叉——某场景一个标准组件都没标
= 该场景 0/N **全缺**（详情页也正是这么显示的）。二者是两回事：一个是「要求」，一个是「产出」。

# 本模块做什么
纯函数 :func:`build_scene_report`：吃 ``required_scenes`` + ``components.html`` → 逐场景「已配齐 X/Y ·
缺哪几件」+ 每件缺失组件「**应包含什么、怎么用标记补**」的可执行指引 + 补齐工作流。供 check_scenes 工具返回，
让分身看到与详情页**一致**的真实覆盖度，并明确知道下一步怎么补——而不是靠 ``get`` 眼看 required_scenes 猜。

# 边界
- 纯函数：只看输入 ``required_scenes`` + HTML，无 IO、无时钟；同输入同输出。
- 场景**标准**（哪些是必须组件、key/中文名）单一事实源在 ``core/scenes.py``（与 Rust 逐字节对齐）；本模块只
  叠加**「人/分身可读的补齐指引 prose」**（``COMPONENT_GUIDANCE``），这是 Python 云端工具专属层，不进
  Rust 对齐面。``test_scene_guidance`` 有 ratchet 守卫：``SCENE_STANDARDS`` 里每个标准组件都必须有指引，
  防止加了组件却漏写指引导致 check_scenes 出参有洞。
"""

from __future__ import annotations

from typing import Any

from backend.app.hasn_designsystem.core.scenes import (
    DEFAULT_REQUIRED_SCENES,
    SCENE_STANDARDS,
    detect_scenes,
    is_known_scene,
)

_SCENE_BY_ID = {s.id: s for s in SCENE_STANDARDS}

# 标记约定说明（returned 给分身，一句话讲清怎么给组件打归属标记）。
MARKER_CONVENTION = (
    '场景容器写 <section data-ds-scene="场景id">…</section>；场景内每件组件写 '
    '<div data-ds-component="组件key">…</div>（归属到文档序上最近的 data-ds-scene；'
    '也可写 data-ds-component="场景id.组件key" 显式限定归属）。只统计**已知场景的已知组件**，'
    '拼错 key 或臆造组件一律被忽略（零 fake，不会假装配齐）。'
)

# ── 每件标准组件「应包含什么」的补齐指引（Python 云端工具专属 prose 层，非 Rust 对齐面）──────────
# key 与 core/scenes.py::SCENE_STANDARDS 逐字对应；分身据此知道每件缺失组件该真正做出什么内容。
# ratchet 守卫（test_scene_guidance）保证 SCENE_STANDARDS 每个组件都在此有条目，避免加组件漏指引。
COMPONENT_GUIDANCE: dict[str, dict[str, str]] = {
    'brand_website': {
        'nav': '顶部导航栏：品牌 logo + 主导航链接 + 主行动按钮（用 --accent 主色点缀）。',
        'hero': 'Hero 首屏：大标题（--font-display）+ 副标题 + 主行动按钮 + 视觉主图/插画，首屏一眼传达价值主张。',
        'features': '特性区：3–4 个特性（图标 + 标题 + 说明）用卡片/网格排布，展示核心能力。',
        'cta': '行动号召 CTA：醒目的转化区块——一句号召文案 + 主按钮（--accent 填充），常配一行副文案。',
        'footer': '页脚：分栏链接 + 版权 + 次级导航/社媒图标，用 --meta/--muted 次级色。',
        'pricing': '定价表（可选加分）：2–3 档价格卡（档名/价格/权益列表/选择按钮），推荐档高亮。',
        'testimonial': '客户评价（可选加分）：客户头像 + 引言 + 姓名/职位的评价卡，建立信任。',
        'faq': '常见问题 FAQ（可选加分）：问答折叠列表（问题 + 答案），消解购买疑虑。',
    },
    'deck': {
        'cover': '封面页：演示文稿首页——大标题 + 副标题 + 品牌标识/日期，撑满画布的视觉焦点。',
        'section': '章节分隔页：章节过渡——大号章节序号/标题 + 简短引导，与正文页明显区分。',
        'bullets': '要点页：标题 + 3–5 条要点列表（可带图标），演示的主力版式。',
        'chart': '数据图表页：标题 + 图表占位（柱/线/饼）+ 关键数字/结论标注。',
        'closing': '结束页：收尾页——致谢/联系方式/号召，风格呼应封面。',
    },
    'poster': {
        'hero_poster': '主视觉海报：竖版主视觉——主标题 + 主视觉图形 + 品牌标识，强视觉冲击。',
        'info_card': '信息卡片：结构化信息卡——标题 + 要点/参数 + 图标，适合活动/产品信息。',
        'social_square': '社媒方图：1:1 方形版式——短标题 + 视觉 + 品牌角标，适配社媒 feed。',
    },
    'mobile': {
        'mobile_nav': '顶部导航：移动端顶栏——返回/标题/操作图标，保证 44px 触达高度。',
        'tab_bar': '底部 Tab 栏：底部标签栏——3–5 个「图标 + 文字」Tab，当前项 --accent 高亮。',
        'list_card': '列表卡片：移动列表项——头像/图标 + 主副文案 + 右侧操作/箭头。',
        'form': '表单：移动表单——字段标签 + 输入框 + 主提交按钮（字段标题比 placeholder 更大更深）。',
        'button_group': '按钮组：成组按钮——主/次/危险按钮各一，展示移动端按钮层级。',
    },
}

# 补齐工作流（returned 给分身；仅在还有场景没配齐时附带）。
NEXT_STEPS: tuple[str, ...] = (
    '1) 编辑 components.html：给每个仍缺的场景补一段 <section data-ds-scene="场景id">…</section>，'
    '把上面列出的每件缺失组件用 <div data-ds-component="组件key">…</div> 标出，并**真正写出该组件的样式与结构**'
    '（沿用本设计系统的 token，绝不硬编码颜色/字号）。',
    '2) 用 hasn.designsystem.extract_components 重新抽取 components.manifest.json（scenes[] 会据新标记刷新）。',
    '3) 用 hasn.designsystem.save 传同一个 design_system_id + 完整 content（含刷新后的 components_html 与 '
    'components_manifest_json）落新一版；required_scenes 不变则不必再传。',
    '4) 再调一次 hasn.designsystem.check_scenes 确认 complete=true，才算把画廊补齐了——'
    '不要仅凭 required_scenes 里「列了这些场景」就对主人说「已配齐」。',
)


def _normalize_required_scenes(raw: Any) -> list[str]:
    """规整 required_scenes：只留已知场景 id、去重保序；空/非法 → 默认 [brand_website]。

    与云端 ``design_system_service._normalize_required_scenes`` / daemon / webui 同义（各引擎各一份纯函数）。
    """
    if not isinstance(raw, (list, tuple)):
        return list(DEFAULT_REQUIRED_SCENES)
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and is_known_scene(item) and item not in seen:
            seen.add(item)
            out.append(item)
    return out or list(DEFAULT_REQUIRED_SCENES)


def _how_to_complete(scene_id: str, missing: list[dict[str, str]]) -> str:
    """为某个未配齐场景生成「怎么补」指引：标记骨架 + 每件缺失组件应包含什么。"""
    guide = COMPONENT_GUIDANCE.get(scene_id, {})
    lines = [
        f'在 components.html 补一段 <section data-ds-scene="{scene_id}">…</section>，'
        f'仍缺 {len(missing)} 件必须组件，各用 <div data-ds-component="组件key">…</div> 标出并真正做出来：'
    ]
    for c in missing:
        hint = guide.get(c['key'], '')
        suffix = f' —— {hint}' if hint else ''
        lines.append(f'  • {c["label"]}（data-ds-component="{c["key"]}"）{suffix}')
    return '\n'.join(lines)


def _summary(scenes: list[dict[str, Any]]) -> str:
    """一行总览：把未配齐场景压成「品牌网站 0/5 · 缺 导航栏/…；演示文稿 0/5 · 缺 …」；全齐则报喜。"""
    partial = [s for s in scenes if not s['complete']]
    if not partial:
        return f'全部要求覆盖的场景已配齐（共 {len(scenes)} 个场景）。'
    parts = [
        f'{s["label"]} {s["present_count"]}/{s["required_total"]} · 缺 ' + '/'.join(m['label'] for m in s['missing'])
        for s in partial
    ]
    return '；'.join(parts)


def build_scene_report(
    required_scenes: Any,
    components_html: str | None,
    *,
    design_system_id: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """交叉 required_scenes × components.html 实检覆盖 → 逐场景覆盖 + 可执行补齐指引（纯函数）。

    - ``required_scenes``：owner 要求覆盖的场景（会经 :func:`_normalize_required_scenes` 规整）。
    - ``components_html``：当前组件画廊 HTML（现读现检测，不依赖可能陈旧的 manifest.scenes[]）。
    - 返回 agent-facing snake_case 报告：``scenes[]`` 逐场景「已配齐 X/Y · 缺哪几件 + how_to_complete」，
      顶层 ``complete`` / ``summary`` / ``marker_convention`` /（未配齐时）``next_steps``。
    """
    normalized = _normalize_required_scenes(required_scenes)
    detected = detect_scenes(components_html or '')
    by_id: dict[str, dict[str, Any]] = {
        s['id']: s for s in detected if isinstance(s, dict) and isinstance(s.get('id'), str)
    }

    scenes: list[dict[str, Any]] = []
    all_complete = True
    for scene_id in normalized:
        std = _SCENE_BY_ID.get(scene_id)
        if std is None:  # 规整后不该出现，防御性跳过
            continue
        got = by_id.get(scene_id, {})
        present_keys = set(got.get('presentComponents') or []) | set(got.get('optionalPresent') or [])
        present = [{'key': c.key, 'label': c.label} for c in std.required if c.key in present_keys]
        missing = [{'key': c.key, 'label': c.label} for c in std.required if c.key not in present_keys]
        optional_present = [{'key': c.key, 'label': c.label} for c in std.optional if c.key in present_keys]
        complete = not missing
        all_complete = all_complete and complete
        scenes.append({
            'id': scene_id,
            'label': std.label,
            'required_total': len(std.required),
            'present_count': len(present),
            'present': present,
            'missing': missing,
            'optional_present': optional_present,
            'complete': complete,
            'how_to_complete': None if complete else _how_to_complete(scene_id, missing),
        })

    report: dict[str, Any] = {
        'design_system_id': design_system_id,
        'name': name,
        'required_scenes': normalized,
        'complete': all_complete,
        'summary': _summary(scenes),
        'scenes': scenes,
        'marker_convention': MARKER_CONVENTION,
    }
    if not all_complete:
        report['next_steps'] = list(NEXT_STEPS)
    return report
