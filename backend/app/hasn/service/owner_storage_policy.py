"""用户云存储类别策略与对象键铸造。

本模块只负责纯策略，不访问数据库或对象存储。业务层必须先解析类别，再把已经
确定的访问级别、存储配置和对象键交给供应商适配层。
"""

from __future__ import annotations

import hashlib
import hmac
import os

from dataclasses import dataclass

from backend.common.exception import errors
from backend.common.response.response_code import StandardResponseCode

MIB = 1024**2
GIB = 1024**3


@dataclass(frozen=True, slots=True)
class CategoryPolicy:
    """一类用户云存储对象的完整策略。"""

    name: str
    access: str
    billable_to_owner: bool
    max_size_bytes: int
    allowed_mime_patterns: tuple[str, ...]
    retention_seconds: int | None
    sign_ttl_seconds: int | None
    owner_writable: bool

    def accepts_mime(self, mime: str) -> bool:
        """判断标准化 MIME 是否符合类别白名单。"""
        normalized = mime.partition(';')[0].strip().lower()
        return any(
            pattern == '*/*'
            or pattern == normalized
            or (pattern.endswith('/*') and normalized.startswith(pattern[:-1]))
            for pattern in self.allowed_mime_patterns
        )

    def assert_upload_allowed(self, *, mime: str, size_bytes: int) -> None:
        """校验文件大小与 MIME，失败返回稳定存储错误码。"""
        if size_bytes <= 0 or size_bytes > self.max_size_bytes:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_413,
                msg='STORAGE_FILE_TOO_LARGE',
                data={
                    'category': self.name,
                    'max_size_bytes': self.max_size_bytes,
                    'requested_bytes': size_bytes,
                },
            )
        if not self.accepts_mime(mime):
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_415,
                msg='STORAGE_MIME_UNSUPPORTED',
                data={'category': self.name, 'mime': mime},
            )


def _policy(
    name: str,
    *,
    access: str,
    billable: bool,
    max_size: int,
    mime: tuple[str, ...],
    retention: int | None = None,
    sign_ttl: int | None = None,
    owner_writable: bool = True,
) -> CategoryPolicy:
    return CategoryPolicy(
        name=name,
        access=access,
        billable_to_owner=billable,
        max_size_bytes=max_size,
        allowed_mime_patterns=mime,
        retention_seconds=retention,
        sign_ttl_seconds=sign_ttl,
        owner_writable=owner_writable,
    )


# 大小上限是服务端安全边界，不是套餐权益；套餐仍由合同快照的 storage_bytes 决定。
CATEGORY_REGISTRY: dict[str, CategoryPolicy] = {
    'dm_attachment': _policy(
        'dm_attachment',
        access='private',
        billable=True,
        max_size=GIB,
        mime=('*/*',),
        sign_ttl=3600,
    ),
    'private_doc': _policy(
        'private_doc',
        access='private',
        billable=True,
        max_size=10 * GIB,
        mime=('*/*',),
        sign_ttl=3600,
    ),
    'published_artifact': _policy(
        'published_artifact',
        access='private',
        billable=True,
        max_size=20 * GIB,
        mime=('*/*',),
        sign_ttl=3600,
    ),
    'user_upload': _policy(
        'user_upload',
        access='private',
        billable=True,
        max_size=20 * GIB,
        mime=('*/*',),
        sign_ttl=3600,
    ),
    'user_avatar': _policy(
        'user_avatar',
        access='public',
        billable=True,
        max_size=10 * MIB,
        mime=('image/*',),
    ),
    'post_image': _policy(
        'post_image',
        access='public',
        billable=True,
        max_size=50 * MIB,
        mime=('image/*',),
    ),
    'platform_package': _policy(
        'platform_package',
        access='public',
        billable=False,
        max_size=20 * GIB,
        mime=('*/*',),
        owner_writable=False,
    ),
    'system_preset': _policy(
        'system_preset',
        access='public',
        billable=False,
        max_size=100 * MIB,
        mime=('image/*', 'application/json', 'application/octet-stream'),
        owner_writable=False,
    ),
    'export_staging': _policy(
        'export_staging',
        access='private',
        billable=False,
        max_size=1024 * GIB,
        mime=('application/zip', 'application/x-tar', 'application/json', 'text/csv'),
        retention=24 * 3600,
        sign_ttl=3600,
        owner_writable=False,
    ),
}

# 兼容层只用于迁移期既有平台写点；新 Owner 写入必须使用规范类别名。
LEGACY_CATEGORY_ALIASES: dict[str, str] = {
    'general_file': 'user_upload',
    'local_source_snapshot': 'published_artifact',
    'film_engine': 'platform_package',
    'release_asset': 'platform_package',
    'speech_model': 'platform_package',
}


def resolve_category(category: str, *, allow_legacy: bool = False) -> CategoryPolicy:
    """解析类别；未知或未授权的旧类别一律 fail-closed。"""
    resolved = LEGACY_CATEGORY_ALIASES.get(category) if allow_legacy else None
    policy = CATEGORY_REGISTRY.get(resolved or category)
    if policy is None:
        raise errors.RequestError(
            code=StandardResponseCode.HTTP_422,
            msg='STORAGE_CATEGORY_UNSUPPORTED',
            data={'category': category},
        )
    return policy


def resolve_owner_category(category: str) -> CategoryPolicy:
    """解析 Owner 可写类别，禁止平台类别与含糊旧类别。"""
    if category == 'general_file':
        raise errors.RequestError(
            code=StandardResponseCode.HTTP_422,
            msg='STORAGE_CATEGORY_UNSUPPORTED',
            data={'category': category},
        )
    policy = resolve_category(category, allow_legacy=True)
    if not policy.owner_writable:
        raise errors.ForbiddenError(
            msg='STORAGE_CATEGORY_FORBIDDEN',
            data={'category': category},
        )
    return policy


def owner_scope(owner_hasn_id: str, *, access: str, salt: str | None = None) -> str:
    """按桶可见性派生 Owner 前缀；公共桶必须使用不可逆 HMAC。"""
    if access == 'private':
        return owner_hasn_id
    if access != 'public':
        raise errors.ServerError(msg='STORAGE_ACCESS_INVALID', data={'access': access})
    secret = salt if salt is not None else os.getenv('OWNER_SCOPE_SALT', '')
    if not secret:
        raise errors.ServerError(msg='STORAGE_OWNER_SCOPE_SALT_MISSING')
    return hmac.new(secret.encode(), owner_hasn_id.encode(), hashlib.sha256).hexdigest()[:16]


def build_owner_object_key(
    *,
    owner_hasn_id: str,
    access: str,
    object_id: str,
    salt: str | None = None,
) -> str:
    """铸造不含文件名、日期和内容摘要的 Owner 隔离对象键。"""
    scope = owner_scope(owner_hasn_id, access=access, salt=salt)
    return f'owners/{scope}/objects/{object_id}'
