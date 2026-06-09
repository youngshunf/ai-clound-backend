"""内置 AI-Native manifest 向后兼容 re-export shim（C6 拆分后）。

manifest 数据已按应用拆到 ``backend/app/hasn/service/builtin_manifests/`` 下
（``community.py`` / ``knowledge.py``）。本模块仅保留为兼容入口——既有导入路径
``from backend.app.hasn.service.ai_native_builtin_manifests import COMMUNITY_AI_NATIVE_MANIFEST``
等不变，且 ``test_ai_native_app_platform`` 的「文件存在性」断言继续成立。

新代码请直接从 ``builtin_manifests.community`` / ``builtin_manifests.knowledge`` 导入。
"""

from __future__ import annotations

from backend.app.hasn.service.builtin_manifests.community import COMMUNITY_AI_NATIVE_MANIFEST
from backend.app.hasn.service.builtin_manifests.knowledge import KNOWLEDGE_AI_NATIVE_MANIFEST

__all__ = ['COMMUNITY_AI_NATIVE_MANIFEST', 'KNOWLEDGE_AI_NATIVE_MANIFEST']
