"""应用平台 façade —— AI-Native 应用平台子系统对外契约（架构候选③ P2）。

application platform 子系统（catalog 目录 / entitlement 权益 / registry 清单 / runtime
gateway 工具派发 / workbench 工作台 / instance resolver 实例解析 / capability guard）是一
个**内聚子系统**，但它的具体单例/类/schema/model 散落在 `app/hasn/service|schema|model`
里，10+ 个兄弟模块各自 `from backend.app.hasn.service.xxx import …` 反向伸进去。

本 façade 把「应用平台对外提供什么」收敛成**单一聚合导出点**：兄弟模块一律
`from backend.app.hasn_core.app_platform import …`，不再直抠 `app/hasn` 内部路径。零实现迁移、
纯 re-export——好处是应用平台对外只有一个门面，后续真要重构其内部（拆 god-file 等）不炸兄弟。

与 P1 身份 façade 一致：re-export 的就是 `app/hasn` 原单例/原类/原 schema/原 model
（实现私有在接缝后），迁移期**零行为变化**。
"""

from __future__ import annotations

# 应用平台对外 schema 契约（pydantic 叶子类型，consumer 均后加载，不入子系统构建图）
from backend.app.hasn.schema.ai_native_runtime import AiNativeToolCallRequest
from backend.app.hasn.schema.hasn_app_entitlement import GetHasnAppEntitlementDetail
from backend.app.hasn.schema.hasn_builtin_task_catalog import BuiltinTaskCatalogResponse

# 服务模块（按属性访问：app_catalog_service.xxx）
from backend.app.hasn.service import app_catalog_service

# 应用平台核心服务单例与类
from backend.app.hasn.service.agent_capability_guard import capability_guard
from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry, ai_native_app_registry
from backend.app.hasn.service.ai_native_knowledge_manifest import KNOWLEDGE_SCOPE_CATALOG
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn.service.app_catalog_service import grant_entitlement
from backend.app.hasn.service.instance_resolver import InstanceResolutionError
from backend.app.hasn.service.workbench_app_registry import workbench_app_registry
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service

# 注意：`WorkbenchApp` 数据类与 `HasnAppInstance`/`HasnAiNativeAppAudit` 模型**不**经本 façade 暴露——
# 它们是被「应用清单（manifest）」在子系统构建期消费的叶子数据类型，manifest 属于应用平台**内部**
# （registry 构建时即 import 各应用 manifest），若 manifest 反过来 import 本 façade 会成环。
# 这类叶子数据类型由其定义处直接 import；本 façade 只聚合应用平台**服务**入口（深接缝、零环）。

__all__ = [
    'AINativeAppRegistry',
    'AiNativeToolCallRequest',
    'BuiltinTaskCatalogResponse',
    'GetHasnAppEntitlementDetail',
    'InstanceResolutionError',
    'KNOWLEDGE_SCOPE_CATALOG',
    'ai_native_app_registry',
    'ai_native_runtime_gateway',
    'app_catalog_service',
    'capability_guard',
    'grant_entitlement',
    'workbench_app_registry',
    'workbench_domain_service',
]
