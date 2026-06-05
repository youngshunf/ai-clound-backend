"""Scope 元数据注册表（catalog 中文化 / 分组 / 风险展示的集中声明）。

设计事实源：13-doc §4.2（scopes.py 集中声明）；platform 部分以 14-doc §3 为权威源。

判定真相仍是各 `BaseTool.required_scopes`（分散但准确）+ 三态 mode；本表只负责
**展示元数据**（中文 label / domain / risk / 描述），通过 scope key 关联。
catalog 渲染缺失元数据时回退到 scope key 本身（不崩、不造假）。
"""

from __future__ import annotations

from typing import Any

# scope_key -> {label_zh, domain, risk, description}
# risk 仅 UI 提示（不强制确认，D4）；社交/平台工具一律 low。
SCOPE_CATALOG: dict[str, dict[str, str]] = {
    # —— platform（14-doc §3 权威）——
    'user:search': {'label_zh': '搜索用户', 'domain': 'user', 'risk': 'low', 'description': '按唤星号/昵称搜索 HASN 用户（人或 Agent）'},
    'user:read': {'label_zh': '查看用户资料', 'domain': 'user', 'risk': 'low', 'description': '查看用户/Agent 主页详情'},
    'contact:read': {'label_zh': '查看联系人', 'domain': 'contact', 'risk': 'low', 'description': '列出主人语境下的联系人与关系状态'},
    'contact:request': {'label_zh': '发送联系请求', 'domain': 'contact', 'risk': 'low', 'description': '向某用户发起加联系/好友请求'},
    'message:read': {'label_zh': '读取/搜索聊天记录', 'domain': 'message', 'risk': 'low', 'description': '读取会话历史、跨会话搜索聊天记录'},
    'message:send': {'label_zh': '发送消息', 'domain': 'message', 'risk': 'low', 'description': '给用户/Agent/会话发消息（走真实路由与关系门控）'},
    'task:create': {'label_zh': '发起任务', 'domain': 'task', 'risk': 'low', 'description': '发起一个任务交给 Runtime 执行'},
    'task:read': {'label_zh': '查看任务进度与结果', 'domain': 'task', 'risk': 'low', 'description': '查任务/会话状态、进度事件与产物'},
    # 兼容历史默认词表（DEFAULT_AGENT_SCOPES）——展示用
    'task:execute': {'label_zh': '执行任务', 'domain': 'task', 'risk': 'low', 'description': '历史默认任务执行权限'},
    'profile:read': {'label_zh': '读取资料', 'domain': 'profile', 'risk': 'low', 'description': '读取自身/主人公开资料'},
    # —— app（builtin AI-Native，与 manifest required_scopes 对齐）——
    'community:read': {'label_zh': '读取社区内容', 'domain': 'community', 'risk': 'low', 'description': '读取社区信息流/帖子/文章/评论/主页/通知'},
    'community:post': {'label_zh': '发布社区内容', 'domain': 'community', 'risk': 'medium', 'description': '以 Agent 身份发帖/发文（按策略审核）'},
    'community:comment': {'label_zh': '评论社区内容', 'domain': 'community', 'risk': 'medium', 'description': '以 Agent 身份评论/回复帖子或文章（按策略审核）'},
    'community:interact': {'label_zh': '社区轻互动', 'domain': 'community', 'risk': 'low', 'description': '以 Agent 身份点赞/关注/收藏（及取消），非创作'},
    'community:circle': {'label_zh': '参与社区圈子', 'domain': 'community', 'risk': 'medium', 'description': '以 Agent 身份加入/退出圈子、在圈内发帖评论（按主人授权与圈策略）'},
    'community:doc': {'label_zh': '创作社区文集', 'domain': 'community', 'risk': 'medium', 'description': '以 Agent 身份建/编辑文集与目录、发文挂文集（默认 private，公开/加密由主人决定）'},
    'knowledge:read': {'label_zh': '检索知识库', 'domain': 'knowledge', 'risk': 'low', 'description': '检索当前工作空间的知识库资料'},
    'knowledge:upload': {'label_zh': '上传知识库文档', 'domain': 'knowledge', 'risk': 'medium', 'description': '向当前工作空间的知识库上传文档（按主人授权与库白名单）'},
    'knowledge:write': {'label_zh': '解析/建库写入', 'domain': 'knowledge', 'risk': 'medium', 'description': '触发文档解析入库、新建数据集（写入知识库结构）'},
    'knowledge:grant': {'label_zh': '代主人改授权', 'domain': 'knowledge', 'risk': 'high', 'description': '代主人调整知识库访问授权（预留，当前不开放）'},
    # —— platform · marketplace（15-技能市场/11-doc 权威源）——
    'marketplace:read': {'label_zh': '浏览能力市场', 'domain': 'marketplace', 'risk': 'low', 'description': '搜索/查看技能与模板、列出当前 Agent 已安装技能'},
    'marketplace:install': {'label_zh': '安装/卸载技能', 'domain': 'marketplace', 'risk': 'medium', 'description': '把市场技能装到当前 Agent 或从中卸载（云端权威 + 重物化）'},
    'marketplace:publish': {'label_zh': '打包与发布资源', 'domain': 'marketplace', 'risk': 'high', 'description': '打包本地技能/模板并发布为当前用户资源（默认草稿；公开/送审过主人确认）'},
    # —— app · presentation（演示文稿 embedded_desktop AI-Native，14-doc/12 §11.12 权威）——
    'presentation:read': {
        'label_zh': '查看演示文稿',
        'domain': 'presentation',
        'risk': 'low',
        'description': '列出/查看演示文稿、查询异步生成任务状态',
    },
    'presentation:create': {
        'label_zh': '生成演示大纲',
        'domain': 'presentation',
        'risk': 'medium',
        'description': '生成演示大纲草稿（消耗 LLM 额度，不导出）',
    },
    'presentation:generate': {
        'label_zh': '生成/编辑演示文稿',
        'domain': 'presentation',
        'risk': 'high',
        'description': '生成/编辑/派生完整演示文稿并导出（消耗 LLM 额度并写入本机文件）',
    },
    'presentation:manage': {
        'label_zh': '管理演示文稿',
        'domain': 'presentation',
        'risk': 'high',
        'description': '删除演示文稿、上传 RAG 文档（破坏性/任意写）',
    },
    'image:generate': {
        'label_zh': '生成图片',
        'domain': 'image',
        'risk': 'medium',
        'description': '直连唤星 new-api 图像 API 生成图片（消耗 owner 配额）',
    },
}

# source 分组的中文标签（catalog 顶层分组）
SOURCE_LABELS: dict[str, str] = {
    'platform': '平台工具',
    'app': '已安装 App',
    'external': '外部 MCP',
}


def scope_meta(scope_key: str) -> dict[str, Any]:
    """取 scope 展示元数据；缺失则回退到 key 本身（不造假）。"""
    meta = SCOPE_CATALOG.get(scope_key)
    if meta:
        return {
            'label': meta['label_zh'],
            'domain': meta.get('domain', ''),
            'risk': meta.get('risk', 'low'),
            'description': meta.get('description', ''),
        }
    domain = scope_key.split(':', 1)[0] if ':' in scope_key else ''
    return {'label': scope_key, 'domain': domain, 'risk': 'low', 'description': ''}
