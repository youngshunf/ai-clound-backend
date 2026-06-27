"""注册期校验：transport × hosting 合法组合矩阵 + namespace 规则（P7）。

事实源 02 §4.1（合法组合矩阵）、10 §4（命名映射与冲突）：

| transport          | remote_service(cloud) | local_process(local) |
|--------------------|-----------------------|----------------------|
| stdio              | ❌（无远程语义）       | ✅（必须本机 spawn） |
| http/ws/sse 远程URL| ✅                    | ✅（也可指回环）     |
| http 127.0.0.1     | ❌                    | ✅                   |

非法组合在**注册时**拒绝，避免调用期才炸（§6 风险）。
"""

from __future__ import annotations

from urllib.parse import urlparse

from backend.app.external_mcp.service.secret_store import is_plaintext_credential
from backend.app.mcp.canonical import EXTERNAL_MARKER, RESERVED_NAMESPACES, validate_canonical_name

HOSTINGS = frozenset({'remote_service', 'local_process'})
TRANSPORTS = frozenset({'http', 'websocket', 'sse', 'stdio'})
ORIGINS = frozenset({'system', 'owner', 'marketplace'})

# 凭据键（值疑似明文则拒绝）：Authorization 头 + 常见 *_KEY/*_TOKEN/*_SECRET。
_CREDENTIAL_KEY_HINTS = ('authorization', 'token', 'key', 'secret', 'password', 'apikey')


class RegistrationError(ValueError):
    """注册校验失败（非法 transport×hosting / namespace 冲突 / 明文凭据）。"""


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower()
    return host in {'127.0.0.1', 'localhost', '::1', '0.0.0.0'} or host.startswith('127.')


def _looks_like_credential_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _CREDENTIAL_KEY_HINTS)


def validate_credential_values(config_map: dict | None, *, where: str) -> None:
    """凭据键的值必须是 secret:// 引用，明文一律拒绝（02 §6）。"""
    if not config_map:
        return
    for key, value in config_map.items():
        if _looks_like_credential_key(str(key)) and is_plaintext_credential(value):
            raise RegistrationError(
                f'{where}.{key} 含明文凭据，必须使用 secret:// 引用（02 §6 加载即校验）'
            )


def validate_server_namespace(name: str) -> str:
    """校验 server_namespace 合法且映射出的 hasn.ext.{name}.* 合规（10 §4）。"""
    name = (name or '').strip()
    if not name:
        raise RegistrationError('server_namespace 不能为空')
    if name == EXTERNAL_MARKER:
        raise RegistrationError("server_namespace 不能是保留标记 'ext'")
    if name in RESERVED_NAMESPACES:
        raise RegistrationError(f"server_namespace '{name}' 撞平台保留 namespace")
    # 以一个探针工具名走 canonical 校验，确保段合法（字母数字下划线）。
    try:
        validate_canonical_name(f'hasn.ext.{name}.__probe__', 'external')
    except ValueError as exc:
        raise RegistrationError(f'非法 server_namespace: {name}（{exc}）') from exc
    return name


def validate_transport_hosting(
    *,
    hosting: str,
    transport: str,
    endpoint: str | None,
    command: str | None,
) -> None:
    """注册期校验 transport × hosting 合法组合 + 端点/命令必填项（02 §4.1）。"""
    if hosting not in HOSTINGS:
        raise RegistrationError(f'非法 hosting: {hosting}')
    if transport not in TRANSPORTS:
        raise RegistrationError(f'非法 transport: {transport}')

    if transport == 'stdio':
        if hosting != 'local_process':
            raise RegistrationError('stdio 传输只能 local_process（无远程语义）')
        if not (command or '').strip():
            raise RegistrationError('local_process stdio 必须配置 command')
        return

    # http / websocket / sse —— 需要 endpoint URL。
    endpoint = (endpoint or '').strip()
    if not endpoint:
        raise RegistrationError(f'{transport} 传输必须配置 endpoint URL')
    parsed = urlparse(endpoint)
    if parsed.scheme not in {'http', 'https', 'ws', 'wss'}:
        raise RegistrationError(f'非法 endpoint scheme: {parsed.scheme}（需 http/https/ws/wss）')

    is_loopback = _is_loopback(parsed.hostname)
    if hosting == 'remote_service' and is_loopback:
        raise RegistrationError('remote_service 不能指向 127.0.0.1/localhost（回环只能 local_process）')
    # local_process 指向远程 URL 或回环都合法（也可指本机回环 http）。
