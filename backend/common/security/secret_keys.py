"""统一秘密字段脱敏常量与方法（对齐 Hermes security.py::SECRET_KEY_FRAGMENTS + daemon P3 ③层）。

设计事实源：
- `docs/产品与技术/技术设计/04-端侧与渠道/第三方IM渠道接入/01-渠道接入上移daemon总体设计.md` §7.1（凭据分层/脱敏分工四层边界）、D10（云端角色）。

该模块为「桌面端第三方 IM 渠道接入」P1 新建，作为云端渠道摘要镜像写库前
（§8.5 第④层兜底）与一致性测试的单一事实源。

⚠️ 有意偏差（防回归）：
- 本模块**不**改写 `backend/app/hermes/service/hermes_agent_app_service.py::_safe_json`。
  后者使用的是 runtime-profile 字段集（含 `runtime_profile_id`/`profile_path`/
  `workspace_path`/`api_server_host`/`api_server_port`/`runtime_token` 等），与渠道
  脱敏语义不同；盲目合并会回归 hermes_agent 同步链路。故本期仅新建本统一模块供渠道域使用，
  并以一致性测试断言本集合与 Hermes `SECRET_KEY_FRAGMENTS` 逐一对齐。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# 逐一对齐 Hermes huanxing_hermes_runtime/security.py::SECRET_KEY_FRAGMENTS
# （api_key / apikey / secret / token / password / credential / client_secret）。
SECRET_KEYS = {
    'api_key',
    'apikey',
    'secret',
    'token',
    'password',
    'credential',
    'client_secret',
}


def is_secret_key(key: str) -> bool:
    """判定某字段名是否为秘密字段（命中精确集，或以 `_secret` / `_token` 结尾）。"""
    k = key.lower()
    return k in SECRET_KEYS or k.endswith(('_secret', '_token'))


def safe_json(value: Any) -> Any:
    """递归剔除秘密字段，序列化 datetime；用于写持久层前的兜底脱敏。

    - dict：丢弃命中 `is_secret_key` 的键，递归处理其余键。
    - list：逐项递归。
    - datetime：转 ISO 字符串（JSONB 友好）。
    - 其它：原样返回。
    """
    if isinstance(value, dict):
        return {k: safe_json(v) for k, v in value.items() if not is_secret_key(k)}
    if isinstance(value, list):
        return [safe_json(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
