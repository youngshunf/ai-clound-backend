"""内置 AI-Native 应用 manifest（按应用拆分，C6 实施）。

设计 §10 迁移 M5：原 ``ai_native_builtin_manifests.py``（1112 行单文件）拆为
每应用一个模块，``ai_native_app_registry`` 直接聚合各应用 manifest，不再 import
单一大文件。``presentation`` 的 manifest 仍在 ``ai_native_builtin_presentation``
（含 WorkbenchApp 构造，待 ADR-15 per-app 目录迁移时一并归位）。

向后兼容：``ai_native_builtin_manifests`` 模块保留为本包的 re-export shim，
现有 ``from ...ai_native_builtin_manifests import COMMUNITY_AI_NATIVE_MANIFEST`` 等
导入路径不变（含 test_ai_native_app_platform 的文件存在性断言）。
"""

from __future__ import annotations

from backend.app.hasn.service.builtin_manifests.community import COMMUNITY_AI_NATIVE_MANIFEST
from backend.app.hasn.service.builtin_manifests.knowledge import KNOWLEDGE_AI_NATIVE_MANIFEST

__all__ = ['COMMUNITY_AI_NATIVE_MANIFEST', 'KNOWLEDGE_AI_NATIVE_MANIFEST']
