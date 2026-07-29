"""获客联系人 PII 的独立版本化加密与 HMAC 密钥环。

密钥由 Vault/KMS 以配置注入，模块不复用全站凭据密钥，也不生成临时 fallback。
加密使用 AES-256-GCM；查重和退订匹配使用独立 HMAC-SHA256 密钥。轮换期新写只用
active 版本，查询同时计算全部保留 HMAC 版本，待旧版本保留期结束后再收缩。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.common.exception import errors
from backend.common.log import log
from backend.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import Mapping


class GrowthPiiKeyConfigurationError(RuntimeError):
    """PII 密钥配置缺失、非法或混用。"""


class GrowthPiiCiphertextError(ValueError):
    """密文格式非法、被篡改或对应版本密钥不可用。"""


@dataclass(frozen=True)
class HmacCandidate:
    """一次多版本 HMAC 查询候选。"""

    version: int
    value: str


def normalize_channel_address(channel: str, value: str) -> str:
    """按渠道规范化地址；只供服务端加密前和 HMAC 匹配使用。"""
    normalized_channel = channel.strip().casefold()
    raw = value.strip()
    if not normalized_channel or not raw:
        raise ValueError('渠道和联系方式不能为空')
    if normalized_channel == 'email' or (normalized_channel == 'all' and '@' in raw):
        if '@' not in raw:
            raise ValueError('邮箱格式无效')
        return raw.casefold()
    if normalized_channel == 'phone' or (
        normalized_channel == 'all' and sum(character.isdigit() for character in raw) >= 8
    ):
        prefix = '+' if raw.startswith('+') else ''
        digits = ''.join(character for character in raw if character.isdigit())
        if len(digits) < 8:
            raise ValueError('手机号格式无效')
        return f'{prefix}{digits}'
    return raw.casefold()


def _decode_key_map(raw: str, *, label: str) -> dict[int, bytes]:
    if not raw.strip():
        raise GrowthPiiKeyConfigurationError(f'{label}密钥未配置')
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GrowthPiiKeyConfigurationError(f'{label}密钥 JSON 无效') from exc
    if not isinstance(parsed, dict):
        raise GrowthPiiKeyConfigurationError(f'{label}密钥必须是版本到 base64 密钥的对象')

    result: dict[int, bytes] = {}
    for version_raw, key_raw in parsed.items():
        try:
            version = int(version_raw)
            key = base64.b64decode(
                str(key_raw).encode('ascii'),
                altchars=b'-_',
                validate=True,
            )
        except (binascii.Error, ValueError, UnicodeError) as exc:
            raise GrowthPiiKeyConfigurationError(f'{label}密钥版本或 base64 无效') from exc
        result[version] = key
    return result


class GrowthPiiKeyring:
    """PII 加密/HMAC 双密钥环；构造时即完成 fail-closed 校验。"""

    def __init__(
        self,
        *,
        encryption_keys: Mapping[int, bytes],
        hmac_keys: Mapping[int, bytes],
        active_encryption_version: int,
        active_hmac_version: int,
    ) -> None:
        self._encryption_keys = dict(encryption_keys)
        self._hmac_keys = dict(hmac_keys)
        self.active_encryption_version = active_encryption_version
        self.active_hmac_version = active_hmac_version
        self._validate()

    @classmethod
    def from_settings(cls) -> GrowthPiiKeyring:
        """从运行时设置构造；配置为空时明确失败。"""
        return cls(
            encryption_keys=_decode_key_map(
                settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
                label='加密',
            ),
            hmac_keys=_decode_key_map(
                settings.GROWTH_PII_HMAC_KEYS_JSON,
                label='HMAC',
            ),
            active_encryption_version=settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
            active_hmac_version=settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
        )

    def _validate(self) -> None:
        if not self._encryption_keys:
            raise GrowthPiiKeyConfigurationError('加密密钥未配置')
        if not self._hmac_keys:
            raise GrowthPiiKeyConfigurationError('HMAC 密钥未配置')
        if self.active_encryption_version not in self._encryption_keys:
            raise GrowthPiiKeyConfigurationError('active 加密密钥版本不存在')
        if self.active_hmac_version not in self._hmac_keys:
            raise GrowthPiiKeyConfigurationError('active HMAC 密钥版本不存在')
        if any(version < 1 for version in (*self._encryption_keys, *self._hmac_keys)):
            raise GrowthPiiKeyConfigurationError('密钥版本必须大于等于 1')
        if any(len(key) != 32 for key in self._encryption_keys.values()):
            raise GrowthPiiKeyConfigurationError('加密密钥必须是 32 字节 AES-256 密钥')
        if any(len(key) < 32 for key in self._hmac_keys.values()):
            raise GrowthPiiKeyConfigurationError('HMAC 密钥至少 32 字节')
        if set(self._encryption_keys.values()) & set(self._hmac_keys.values()):
            raise GrowthPiiKeyConfigurationError('加密密钥与 HMAC 密钥不得复用')

    @staticmethod
    def _aad(*, version: int, purpose: str) -> bytes:
        return f'hasn-growth-pii:v{version}:{purpose}'.encode()

    def encrypt(self, plaintext: str, *, purpose: str) -> str:
        """用 active AES-GCM 密钥加密并返回 urlsafe base64 密文。"""
        if not plaintext:
            raise ValueError('PII 明文不能为空')
        version = self.active_encryption_version
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._encryption_keys[version]).encrypt(
            nonce,
            plaintext.encode(),
            self._aad(version=version, purpose=purpose),
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode('ascii')

    def decrypt(self, ciphertext: str, *, version: int, purpose: str) -> str:
        """按持久化版本解密；未知版本、篡改或格式错误统一 fail-closed。"""
        key = self._encryption_keys.get(version)
        if key is None:
            raise GrowthPiiCiphertextError('对应加密密钥版本不可用')
        try:
            payload = base64.b64decode(
                ciphertext.encode('ascii'),
                altchars=b'-_',
                validate=True,
            )
            nonce, encrypted = payload[:12], payload[12:]
            if len(nonce) != 12 or not encrypted:
                raise ValueError
            return (
                AESGCM(key)
                .decrypt(
                    nonce,
                    encrypted,
                    self._aad(version=version, purpose=purpose),
                )
                .decode()
            )
        except (binascii.Error, InvalidTag, UnicodeError, ValueError) as exc:
            raise GrowthPiiCiphertextError('PII 密文无效或已被篡改') from exc

    def hmac_for(self, channel: str, address: str, *, version: int | None = None) -> str:
        """计算指定版本的 HMAC；缺省仅用于新写的 active 版本。"""
        target_version = self.active_hmac_version if version is None else version
        key = self._hmac_keys.get(target_version)
        if key is None:
            raise GrowthPiiKeyConfigurationError('对应 HMAC 密钥版本不可用')
        normalized_channel = channel.strip().casefold()
        normalized_address = normalize_channel_address(normalized_channel, address)
        message = f'hasn-growth-contact:{normalized_channel}:{normalized_address}'.encode()
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def hmac_candidates(self, channel: str, address: str) -> tuple[HmacCandidate, ...]:
        """轮换期为当前和全部保留 HMAC 版本计算查询候选。"""
        return tuple(
            HmacCandidate(version=version, value=self.hmac_for(channel, address, version=version))
            for version in sorted(self._hmac_keys, reverse=True)
        )


@cache
def get_growth_pii_keyring() -> GrowthPiiKeyring:
    """进程级缓存经校验的密钥环；轮换后需滚动进程加载新版本。"""
    return GrowthPiiKeyring.from_settings()


def require_growth_pii_keyring() -> GrowthPiiKeyring:
    """加载有效密钥环；配置错误时记录关键故障并拒绝继续处理 PII。"""
    try:
        return get_growth_pii_keyring()
    except GrowthPiiKeyConfigurationError as exc:
        log.error('[GrowthPII] PII 密钥配置不可用，已拒绝敏感数据操作')
        raise errors.ServerError(
            msg='联系人私有数据服务暂不可用',
            data={'error_code': 'GROWTH_PII_KEYRING_UNAVAILABLE'},
        ) from exc
