"""网页发布（publish / 模块 18）scope 展示元数据。

设计事实源：模块 18（网页发布）；16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，
由 `app/mcp/scopes.py` 聚合）。判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

写类统一 `publish:write`；读类（get/list）无 required_scopes，但保留 `publish:read` 词表展示元数据。
"""

from __future__ import annotations

PUBLISH_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'publish:read': {'label_zh': '查看发布内容', 'label_en': 'View published pages', 'domain': 'publish', 'risk': 'low', 'description': '列出/查看主人的网页发布与分享链接（hasn.publish.get/list）', 'description_en': "List and view the owner's web publications and share links (hasn.publish.get/list)"},
    'publish:write': {'label_zh': '发布与管理网页', 'label_en': 'Publish and manage pages', 'domain': 'publish', 'risk': 'medium', 'description': '创建/更新/删除网页发布、改可见性、生成分享链接（hasn.publish.create/update/set_visibility/revoke/delete）', 'description_en': 'Create, update, and delete web publications, change visibility, and generate share links (hasn.publish.create/update/set_visibility/revoke/delete)'},
}
