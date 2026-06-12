"""knowledge（知识库）scope 展示元数据。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §3.3；16-工具授权统一 D-v3-3
（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。
"""

from __future__ import annotations

KNOWLEDGE_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'knowledge:read': {
        'label_zh': '检索知识库',
        'domain': 'knowledge',
        'risk': 'low',
        'description': '检索/读取主人的知识库资料（按库白名单裁剪）',
    },
    'knowledge:upload': {
        'label_zh': '上传知识库文档',
        'domain': 'knowledge',
        'risk': 'medium',
        'description': '向主人的知识库上传文档（按主人授权与库白名单）',
    },
    'knowledge:write': {
        'label_zh': '写知识库文档',
        'domain': 'knowledge',
        'risk': 'medium',
        'description': '创建/更新知识库原生文档（保存即重建索引）',
    },
}
