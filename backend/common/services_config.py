"""services_config —— 内部服务「结构化部署配置 + 令牌集中派生」。

**解决两件事**（取代 `.env` 里逐服务散配的扁平键 `*_SERVICE_URL/TOKEN/TIMEOUT`）：

1. **结构化**：部署值放进一个 TOML 文件 ``huanxing-apps/services.toml``，每服务一个
   ``[service.<name>]`` 块（``url`` / ``timeout`` 可选覆盖），服务再多也一目了然、不膨胀。
2. **令牌集中**：只配**一个** ``master_secret``，每服务令牌 = ``HMAC-SHA256(master_secret, 服务名)``
   自动派生（:func:`derive_service_token`）。云端发请求按服务名算 token、服务端按自己的名字算同一
   token 来校验——**两边无需各配、无需保持同步**。新增服务零 token 配置（详见 doc25）。

**文件查找**：env ``HUANXING_SERVICES_CONFIG``（显式路径）> 从本文件向上找 ``huanxing-apps/services.toml``。
缺文件 = 静默空配（prod 可只用 env ``HUANXING_INTERNAL_SERVICE_SECRET`` 注入主密钥，不依赖文件）。
**env 同名变量始终优先**（CI / 容器注入 / 运行时覆盖），保证向后兼容。

注意：:func:`derive_service_token` 的算法**必须与各内部服务端实现逐字节一致**（finance/quant 的
``services_config`` 各持一份等价实现，因属独立 uv 项目无法共享模块）。改算法须三处同改。
"""

from __future__ import annotations

import hashlib
import hmac
import os

from functools import lru_cache
from pathlib import Path

try:  # tomllib 为 3.11+ 标准库；旧解释器优雅降级为「无文件」（仅靠 env）。
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 运行时为 3.12
    tomllib = None  # type: ignore[assignment]

_ENV_CONFIG_PATH = 'HUANXING_SERVICES_CONFIG'
_ENV_MASTER_SECRET = 'HUANXING_INTERNAL_SERVICE_SECRET'
_CONFIG_FILENAME = 'services.toml'


def derive_service_token(master_secret: str, service_name: str) -> str:
    """派生内部服务令牌：``HMAC-SHA256(master_secret, 服务名)`` 的十六进制串。

    主密钥为空 → 返回 ``''``（令牌闸关闭，仅 dev）。**此实现须与各服务端逐字节一致。**
    """
    if not master_secret:
        return ''
    return hmac.new(master_secret.encode('utf-8'), service_name.encode('utf-8'), hashlib.sha256).hexdigest()


def _find_config_file() -> Path | None:
    explicit = os.environ.get(_ENV_CONFIG_PATH, '').strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    for parent in Path(__file__).resolve().parents:
        for candidate in (parent / _CONFIG_FILENAME, parent / 'huanxing-apps' / _CONFIG_FILENAME):
            if candidate.is_file():
                return candidate
    return None


@lru_cache(maxsize=1)
def _load() -> dict:
    """读取并缓存 services.toml（缺文件 / 解析失败 / 无 tomllib → 空配，仅靠 env）。"""
    if tomllib is None:
        return {}
    path = _find_config_file()
    if not path:
        return {}
    try:
        with path.open('rb') as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def reload_services_config() -> None:
    """清缓存（测试用 / 配置热更后强制重读）。"""
    _load.cache_clear()


def master_secret() -> str:
    """主密钥：env ``HUANXING_INTERNAL_SERVICE_SECRET`` 优先，回退 services.toml ``master_secret``。"""
    return (os.environ.get(_ENV_MASTER_SECRET) or _load().get('master_secret') or '').strip()


def service_overrides(name: str) -> dict:
    """某服务在 services.toml 的 ``[service.<name>]`` 覆盖块（无则空 dict）。"""
    section = _load().get('service')
    if not isinstance(section, dict):
        return {}
    block = section.get(name)
    return block if isinstance(block, dict) else {}
