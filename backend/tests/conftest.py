"""pytest 会话级前置（tests 根 conftest·对 backend/tests/ 下任意用例都生效）。

G6 统一资源权限门 S2-5 在 `ResourceShareService.upsert_share` 建 share 行前 fail-closed 校验
`resource_type` 已注册 G6 adapter（「能分享、必能判」·doc33 S2-5）。生产由 app 启动 import 路由链
（→ `ai_native_app_registry`）把全部应用 adapter 注册进进程级单例；但测试若**隔离**运行某个 share
用例、其进程未 import 到该注册链，会令 upsert_share 把合法分享误判为「未注册」而拒。

故在此 module-level import 权威注册点 `ai_native_app_registry`（其顶部 import 各应用
`resource_adapter` 触发自注册），一次性把全部 G6 资源适配器注册进单例，令 S2-5 在任何运行模式
（全量 / 单文件 / 单用例）下都不会误伤合法分享。新增应用只要把 adapter 接进 ai_native_app_registry，
本 conftest 即自动覆盖，无需在此维护清单。
"""

from __future__ import annotations

import backend.app.hasn.service.ai_native_app_registry  # noqa: F401  # import 即注册全部 G6 资源适配器
