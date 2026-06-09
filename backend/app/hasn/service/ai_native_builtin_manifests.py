"""内置 AI-Native manifest 向后兼容 re-export shim（C6 拆分后）。

manifest 数据已按应用归位到各应用目录（ADR-15「每应用独立目录」）：
- ``community`` → ``backend/app/hasn_community/service/ai_native_manifest.py``（社区已是独立模块）；
- ``knowledge`` → ``backend/app/hasn/service/ai_native_knowledge_manifest.py``（知识库尚未拆出独立模块，
  暂留 ``app/hasn``，待其域迁移时随 ADR-15 §3.4 归位 ``app/knowledge``）。
- ``presentation`` 在 ``backend/app/hasn/service/ai_native_builtin_presentation.py``（同上待归位）。

本模块仅保留为兼容入口——既有导入路径
``from backend.app.hasn.service.ai_native_builtin_manifests import COMMUNITY_AI_NATIVE_MANIFEST``
等不变，且 ``test_ai_native_app_platform`` 的「文件存在性」断言继续成立。
新代码请直接从各应用目录导入。
"""

from __future__ import annotations

from backend.app.hasn.service.ai_native_knowledge_manifest import KNOWLEDGE_AI_NATIVE_MANIFEST
from backend.app.hasn_community.service.ai_native_manifest import COMMUNITY_AI_NATIVE_MANIFEST

__all__ = ['COMMUNITY_AI_NATIVE_MANIFEST', 'KNOWLEDGE_AI_NATIVE_MANIFEST']
