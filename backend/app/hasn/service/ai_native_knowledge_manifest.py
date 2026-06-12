"""knowledge manifest 向后兼容 re-export shim（知识库拆出独立模块 app/hasn_knowledge 后）。

manifest 与 scope 元数据已随 ADR-15 归位应用目录：
- manifest → ``backend/app/hasn_knowledge/manifest.py``；
- scope 元数据 → ``backend/app/hasn_knowledge/scopes.py``。

本模块仅保留为兼容入口（既有导入路径不变）；新代码请直接从 ``backend.app.hasn_knowledge`` 导入。
P2 cutover 验证后可随旧导入点一并删除。
"""

from __future__ import annotations

from backend.app.hasn_knowledge.manifest import KNOWLEDGE_AI_NATIVE_MANIFEST
from backend.app.hasn_knowledge.scopes import KNOWLEDGE_SCOPE_CATALOG

__all__ = ['KNOWLEDGE_AI_NATIVE_MANIFEST', 'KNOWLEDGE_SCOPE_CATALOG']
