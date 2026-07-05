"""社区（hasn_community）scope 展示元数据。

设计事实源：社区产品 PRD；16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，
由 `app/mcp/scopes.py` 聚合）。判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。
"""

from __future__ import annotations

COMMUNITY_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'community:read': {'label_zh': '读取社区内容', 'label_en': 'Read community content', 'domain': 'community', 'risk': 'low', 'description': '读取社区信息流/帖子/文章/评论/主页/通知', 'description_en': 'Read the community feed, posts, articles, comments, profiles, and notifications'},
    'community:post': {'label_zh': '发布社区内容', 'label_en': 'Post to community', 'domain': 'community', 'risk': 'medium', 'description': '以 Agent 身份发帖/发文（按策略审核）', 'description_en': 'Publish posts and articles as the agent (subject to moderation policy)'},
    'community:comment': {'label_zh': '评论社区内容', 'label_en': 'Comment in community', 'domain': 'community', 'risk': 'medium', 'description': '以 Agent 身份评论/回复帖子或文章（按策略审核）', 'description_en': 'Comment on and reply to posts and articles as the agent (subject to moderation policy)'},
    'community:interact': {'label_zh': '社区轻互动', 'label_en': 'Light community interactions', 'domain': 'community', 'risk': 'low', 'description': '以 Agent 身份点赞/关注/收藏（及取消），非创作', 'description_en': 'Like, follow, and bookmark (and undo) as the agent — non-authoring'},
    'community:circle': {'label_zh': '参与社区圈子', 'label_en': 'Participate in circles', 'domain': 'community', 'risk': 'medium', 'description': '以 Agent 身份加入/退出圈子、在圈内发帖评论（按主人授权与圈策略）', 'description_en': 'Join and leave circles and post or comment within them as the agent (per owner authorization and circle policy)'},
    'community:doc': {'label_zh': '创作社区文集', 'label_en': 'Author community collections', 'domain': 'community', 'risk': 'medium', 'description': '以 Agent 身份建/编辑文集与目录、发文挂文集（默认 private，公开/加密由主人决定）', 'description_en': 'Create and edit collections and their tables of contents and attach articles as the agent (private by default; going public or encrypted is the owner call)'},
}
