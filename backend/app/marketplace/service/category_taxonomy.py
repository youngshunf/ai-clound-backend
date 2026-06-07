"""技能市场分类权威清单 + 归一化（单一事实源）。

背景：分类历史上越拆越细（媒体被拆成 媒体/图片/音频/视频、数据拆成 分析/处理、
内容拆成 内容创作/写作助手、工具拆成 效率/实用/自动化、沟通拆成 沟通协作/社交媒体），
共 21 个 chip，过多过细。本模块把分类**按领域合并为 12 个领域 + 其他**，并提供
`normalize_category()` 供 github / clawhub 两条同步链路统一落到权威 slug。

合并迁移见 `backend/sql/migrations/2026-06-07-consolidate-marketplace-categories.sql`，
权威种子见 `backend/sql/tables/marketplace_category.sql`，三者必须保持一致。
"""

from __future__ import annotations

from typing import Any, Final

# 权威分类（slug + 中文显示名 + emoji + 策展排序）。
# webui 统一分类选择器（技能 / 技能包 / 分身模板三页）按 sort_order 升序展示。
CANONICAL_CATEGORIES: Final[list[dict[str, Any]]] = [
    {'slug': 'content-creation', 'name': '内容创作', 'icon': '✍️', 'sort_order': 1},
    {'slug': 'creativity', 'name': '设计创意', 'icon': '🎨', 'sort_order': 2},
    {'slug': 'media', 'name': '媒体处理', 'icon': '🎬', 'sort_order': 3},
    {'slug': 'development', 'name': '开发工具', 'icon': '💻', 'sort_order': 4},
    {'slug': 'data-analysis', 'name': '数据分析', 'icon': '📊', 'sort_order': 5},
    {'slug': 'productivity', 'name': '效率办公', 'icon': '⚡', 'sort_order': 6},
    {'slug': 'ai-assistant', 'name': 'AI 助手', 'icon': '🤖', 'sort_order': 7},
    {'slug': 'communication', 'name': '沟通社交', 'icon': '💬', 'sort_order': 8},
    {'slug': 'search', 'name': '搜索检索', 'icon': '🔍', 'sort_order': 9},
    {'slug': 'finance', 'name': '金融理财', 'icon': '💰', 'sort_order': 10},
    {'slug': 'health', 'name': '健康医疗', 'icon': '🏥', 'sort_order': 11},
    {'slug': 'entertainment', 'name': '娱乐休闲', 'icon': '🎮', 'sort_order': 12},
    {'slug': 'other', 'name': '其他', 'icon': '📦', 'sort_order': 99},
]

CANONICAL_SLUGS: Final[frozenset[str]] = frozenset(c['slug'] for c in CANONICAL_CATEGORIES)

DEFAULT_CATEGORY: Final[str] = 'other'

# 任意原始分类值（旧 slug / huanxing-skills 目录名 / 中文显示名 / 近义词）→ 权威 slug。
# 键统一用小写匹配（中文小写化为自身），未命中则回退 DEFAULT_CATEGORY。
CATEGORY_ALIASES: Final[dict[str, str]] = {
    # —— 内容创作 ——
    'writing': 'content-creation',
    'write': 'content-creation',
    'content': 'content-creation',
    'copywriting': 'content-creation',
    'blog': 'content-creation',
    'article': 'content-creation',
    '写作助手': 'content-creation',
    '写作': 'content-creation',
    '内容创作': 'content-creation',
    '文案': 'content-creation',
    # —— 设计创意 ——
    'creative': 'creativity',
    'design': 'creativity',
    'art': 'creativity',
    '创意设计': 'creativity',
    '设计创意': 'creativity',
    '设计': 'creativity',
    # —— 媒体处理 ——
    'image': 'media',
    'images': 'media',
    'photo': 'media',
    'picture': 'media',
    'audio': 'media',
    'music': 'media',
    'sound': 'media',
    'video': 'media',
    'movie': 'media',
    'multimedia': 'media',
    '图片处理': 'media',
    '图片': 'media',
    '音频处理': 'media',
    '音频': 'media',
    '视频创作': 'media',
    '视频': 'media',
    '媒体处理': 'media',
    '媒体': 'media',
    # —— 开发工具 ——
    'developer': 'development',
    'dev': 'development',
    'code': 'development',
    'programming': 'development',
    '开发工具': 'development',
    '开发': 'development',
    # —— 数据分析 ——
    'data': 'data-analysis',
    'analytics': 'data-analysis',
    'analysis': 'data-analysis',
    'database': 'data-analysis',
    '数据处理': 'data-analysis',
    '数据分析': 'data-analysis',
    '数据': 'data-analysis',
    # —— 效率办公 ——
    'utility': 'productivity',
    'utilities': 'productivity',
    'efficiency': 'productivity',
    'automation': 'productivity',
    'workflow': 'productivity',
    'tool': 'productivity',
    'tools': 'productivity',
    'office': 'productivity',
    '实用工具': 'productivity',
    '效率工具': 'productivity',
    '自动化': 'productivity',
    '办公': 'productivity',
    # —— AI 助手 ——
    'assistant': 'ai-assistant',
    'ai': 'ai-assistant',
    'llm': 'ai-assistant',
    'ai 助手': 'ai-assistant',
    'ai助手': 'ai-assistant',
    '智能助手': 'ai-assistant',
    # —— 沟通社交 ——
    'social': 'communication',
    'marketing': 'communication',
    'chat': 'communication',
    '沟通协作': 'communication',
    '社交媒体': 'communication',
    '社交': 'communication',
    '营销推广': 'communication',
    # —— 搜索检索 ——
    'retrieval': 'search',
    'lookup': 'search',
    '搜索检索': 'search',
    '搜索': 'search',
    # —— 垂直领域 ——
    '金融理财': 'finance',
    'invest': 'finance',
    'trading': 'finance',
    '健康医疗': 'health',
    'medical': 'health',
    'fitness': 'health',
    'game': 'entertainment',
    'fun': 'entertainment',
    '娱乐休闲': 'entertainment',
    '娱乐': 'entertainment',
    # —— 其他 ——
    'official': 'other',
    'agent': 'other',
    'misc': 'other',
    '其他': 'other',
}


def normalize_category(raw: Any, *, default: str | None = DEFAULT_CATEGORY) -> str | None:
    """把任意原始分类值归一到权威 slug。

    命中顺序：权威 slug 直通 → 小写权威 slug → 别名表 → 回退 default。
    `default=None` 时未命中返回 None（github 无 frontmatter 的技能保留 None 交给 LLM 分类）。
    """
    if raw is None:
        return default
    value = str(raw).strip()
    if not value:
        return default
    if value in CANONICAL_SLUGS:
        return value
    key = value.lower()
    if key in CANONICAL_SLUGS:
        return key
    if key in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[key]
    return default
