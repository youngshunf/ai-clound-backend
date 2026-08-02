"""能力市场平台资源描述符。

能力市场是平台核心模块，不是可购买的 AI-Native 应用，因此这里只声明资源寻址与产物卡片，
不进入应用目录、发布流程或权限目录。
"""

from __future__ import annotations

from typing import Any


MARKETPLACE_RESOURCE_MANIFEST: dict[str, Any] = {
    'app_id': 'marketplace',
    'resources': [
        {
            'resource_kind': 'marketplace.skill',
            'uri_domain': 'marketplace/skills',
            'ref_type': 'skill',
            'open': {'mode': 'internal_route', 'route_template': '/marketplace/skills/:id'},
            'card': {'verb': '技能', 'action_label': '打开技能'},
        },
        {
            'resource_kind': 'marketplace.template',
            'uri_domain': 'marketplace/templates',
            'ref_type': 'template',
            'open': {'mode': 'internal_route', 'route_template': '/marketplace/templates/:id'},
            'card': {'verb': '分身模板', 'action_label': '打开分身模板'},
        },
        {
            'resource_kind': 'marketplace.skill_pack',
            'uri_domain': 'marketplace/skill-packs',
            'ref_type': 'skill_pack',
            'open': {'mode': 'internal_route', 'route_template': '/marketplace/bundles/:id'},
            'card': {'verb': '技能包', 'action_label': '打开技能包'},
        },
    ],
}


__all__ = ['MARKETPLACE_RESOURCE_MANIFEST']
