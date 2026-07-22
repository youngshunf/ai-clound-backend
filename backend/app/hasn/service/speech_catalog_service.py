"""通用语音模型签名目录服务（云端权威·哑存储·单行下发）SPCAT-4。

职责：
  - get_node_response：节点拉取签名 catalog + revision（无行时返回空，daemon 保持未装配）。
  - stage_package_upload：流式暂存服务端内容寻址 ZIP，并复核真实对象。
  - publish_release：全部引用通过后，在单一 PostgreSQL 事务切换不可变 release head。

安全模型（同 hasn_release minisign 哲学）：发布方离线 Ed25519 私钥签名，云端**只哑存储 + 下发**，
不验签、不改写。daemon 持内置公钥自行验签才是安全执行点。故：
  - catalog_json 存**逐字节原文**（不解析后重序列化）；daemon verify 会 serde 反序列化 payload
    重算签名，任何字段增删/JSON 归一都会破坏验签。
  - 云端仅做「一致性预检」（真实对象 sha256/大小与 catalog 声明相符、URL 与落桶直链相符、https），
    早暴露发布方失误，**不代替** daemon 验签。
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn.model.hasn_speech_catalog import HasnSpeechCatalog
from backend.app.hasn.model.hasn_speech_catalog_release import HasnSpeechCatalogRelease
from backend.app.hasn.model.hasn_speech_catalog_release_package import (
    HasnSpeechCatalogReleasePackage,
)
from backend.app.hasn.model.hasn_speech_package import HasnSpeechPackage
from backend.app.hasn.schema.hasn_speech_catalog import (
    SpeechCatalogModelSummary,
    SpeechCatalogNodeResponse,
    SpeechCatalogPublishResponse,
    SpeechPackageStageResponse,
)
from backend.common.exception import errors
from backend.plugin.s3.model import S3Storage
from backend.plugin.s3.service.storage_service import (
    SPEECH_STORAGE_LOCK_KEY,
    ObjectRef,
    StorageService,
)
from backend.plugin.s3.utils.file_ops import build_object_url, sha256_object, stat_object

if TYPE_CHECKING:
    from fastapi import UploadFile
    from sqlalchemy.ext.asyncio import AsyncSession

# 单行权威键
_CONFIG_KEY = 'global'
# 模型 zip 落公开桶的类别（storage_service CATEGORY_POLICY：public·不签名·长效 https）
_STORAGE_CATEGORY = 'speech_model'
_SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')
_SIGNATURE_PATTERN = re.compile(r'^[0-9a-f]{128}$')
_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_V2_ENVELOPE_FIELDS = {'payload', 'key_id', 'release_sequence', 'expires_at', 'signature'}
# PostgreSQL 事务级 advisory lock，串行化全局唯一语音目录 head 的发布判定。
_RELEASE_LOCK_KEY = SPEECH_STORAGE_LOCK_KEY
# FastAPI UploadFile 会落临时文件；这里再设业务上限，防止无限大对象占满磁盘和上传连接。
MAX_SPEECH_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_StorageSnapshot = tuple[str, str, str, str, str, str, str | None, str | None, str | None, str | None]
_PackageSnapshot = tuple[int, int, str, int, str, _StorageSnapshot]


@dataclass(frozen=True, slots=True)
class _PackageVerificationInput:
    """释放数据库连接前复制出的包登记与对象存储配置。"""

    package_id: int
    sha256: str
    storage_id: int
    object_key: str
    size: int
    object_etag: str | None
    storage: S3Storage


def _storage_snapshot(storage: S3Storage) -> _StorageSnapshot:
    """复制会影响真实对象位置和稳定 URL 的全部存储配置。"""
    return (
        storage.endpoint,
        storage.access_key,
        storage.secret_key,
        storage.bucket,
        storage.access,
        storage.sign_strategy,
        storage.prefix,
        storage.region,
        storage.cdn_domain,
        storage.remark,
    )


def _copy_storage(storage: S3Storage) -> S3Storage:
    """复制远程对象访问所需配置，避免事务释放后访问已过期 ORM 行。"""
    copied = S3Storage(
        name=storage.name,
        endpoint=storage.endpoint,
        access_key=storage.access_key,
        secret_key=storage.secret_key,
        bucket=storage.bucket,
        access=storage.access,
        sign_strategy=storage.sign_strategy,
        prefix=storage.prefix,
        region=storage.region,
        cdn_domain=storage.cdn_domain,
        remark=storage.remark,
    )
    copied.id = storage.id
    return copied


@dataclass(frozen=True, slots=True)
class SpeechCatalogReleasePackageReference:
    """签名 catalog 中的一条平台包引用。"""

    model_id: str
    model_version: str
    os: str
    arch: str
    acceleration: str
    url: str
    sha256: str
    compressed_size: int
    installed_size: int
    license_name: str
    license_url: str
    source_url: str


@dataclass(frozen=True, slots=True)
class SpeechCatalogReleaseManifest:
    """通过云端结构预检的 v2 发布信封。"""

    key_id: str
    release_sequence: int
    expires_at: datetime
    catalog_version: str
    issued_at: datetime
    payload: dict[str, Any]
    packages: tuple[SpeechCatalogReleasePackageReference, ...]


@dataclass(frozen=True, slots=True)
class StagedSpeechPackageEvidence:
    """云端已经成功写入不可变对象存储的模型包证据。"""

    sha256: str
    object_key: str
    stable_url: str
    size: int


def _require_sha256(value: object) -> str:
    """校验并返回规范小写 SHA-256。"""
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise errors.RequestError(msg='模型包 sha256 必须是 64 位规范小写十六进制')
    return value


def build_speech_package_object_key(sha256: str) -> str:
    """从规范小写 SHA-256 派生不可变对象 key。"""
    sha256 = _require_sha256(sha256)
    return f'speech/sha256/{sha256[:2]}/{sha256}.zip'


def _parse_rfc3339(value: object, *, field: str) -> datetime:
    """解析必须携带时区的 RFC3339 时间。"""
    if not isinstance(value, str) or not value:
        raise errors.RequestError(msg=f'{field} 必须是 RFC3339 时间')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise errors.RequestError(msg=f'{field} 必须是 RFC3339 时间') from exc
    if parsed.tzinfo is None:
        raise errors.RequestError(msg=f'{field} 必须携带时区')
    return parsed


def _require_identifier(value: object, *, field: str) -> str:
    """校验与 daemon 相同范围的稳定 ASCII 标识符。"""
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise errors.RequestError(msg=f'{field} 必须是 1～64 位稳定 ASCII 标识符')
    return value


def _require_https_url(value: object, *, field: str) -> str:
    """校验生产目录中的不可过期 HTTPS 直链。"""
    if not isinstance(value, str):
        raise errors.RequestError(msg=f'{field} 必须是 HTTPS URL')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.fragment:
        raise errors.RequestError(msg=f'{field} 必须是无片段的 HTTPS URL')
    return value


def _require_positive_int(value: object, *, field: str, maximum: int = 2**64 - 1) -> int:
    """校验 Rust u64 范围内的正整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise errors.RequestError(msg=f'{field} 必须是大于 0 的 u64')
    return value


def _parse_release_package(
    package: object,
    *,
    model_id: str,
    model_version: str,
    license_name: str,
    license_url: str,
    source_url: str,
) -> SpeechCatalogReleasePackageReference:
    """解析并预检一条平台包引用。"""
    if not isinstance(package, dict):
        raise errors.RequestError(msg=f'{model_id} 的 package 必须是对象')
    platform = package.get('platform')
    if not isinstance(platform, dict):
        raise errors.RequestError(msg=f'{model_id} 的 package.platform 必须是对象')
    os_name = _require_identifier(platform.get('os'), field=f'{model_id}.platform.os')
    arch = _require_identifier(platform.get('arch'), field=f'{model_id}.platform.arch')
    acceleration = _require_identifier(platform.get('acceleration'), field=f'{model_id}.platform.acceleration')

    sha256 = _require_sha256(package.get('sha256'))
    object_key = build_speech_package_object_key(sha256)
    url = _require_https_url(package.get('url'), field=f'{model_id}.package.url')
    if not urlsplit(url).path.rstrip('/').endswith(f'/{object_key}'):
        raise errors.RequestError(msg=f'{model_id} 的包 URL 不是 sha256 内容寻址地址')
    package_signature = package.get('signature')
    if not isinstance(package_signature, str) or _SIGNATURE_PATTERN.fullmatch(package_signature) is None:
        raise errors.RequestError(msg=f'{model_id} 的 package.signature 非法')

    return SpeechCatalogReleasePackageReference(
        model_id=model_id,
        model_version=model_version,
        os=os_name,
        arch=arch,
        acceleration=acceleration,
        url=url,
        sha256=sha256,
        compressed_size=_require_positive_int(package.get('compressed_size'), field=f'{model_id}.compressed_size'),
        installed_size=_require_positive_int(package.get('installed_size'), field=f'{model_id}.installed_size'),
        license_name=license_name,
        license_url=license_url,
        source_url=source_url,
    )


def _parse_release_model(
    model: object,
    *,
    release_sequence: int,
    release_expires_at: object,
) -> tuple[tuple[str, str], list[SpeechCatalogReleasePackageReference]]:
    """解析一个模型及其全部平台包引用。"""
    if not isinstance(model, dict):
        raise errors.RequestError(msg='catalog model 必须是对象')
    model_id = _require_identifier(model.get('model_id'), field='model_id')
    model_version = _require_identifier(model.get('model_version'), field='model_version')
    if model.get('release_sequence') != release_sequence:
        raise errors.RequestError(msg=f'{model_id} 的 release_sequence 与 v2 信封不一致')
    _require_identifier(model.get('channel'), field=f'{model_id}.channel')
    if model.get('expires_at') != release_expires_at:
        raise errors.RequestError(msg=f'{model_id} 的 expires_at 与 v2 信封不一致')

    license_metadata = model.get('license')
    if not isinstance(license_metadata, dict):
        raise errors.RequestError(msg=f'{model_id} 缺少许可证元数据')
    license_name = license_metadata.get('name')
    if not isinstance(license_name, str) or not license_name.strip():
        raise errors.RequestError(msg=f'{model_id} 缺少许可证名称')
    license_url = _require_https_url(license_metadata.get('url'), field=f'{model_id} 许可证 URL')
    source_url = _require_https_url(license_metadata.get('source'), field=f'{model_id} 许可证来源')

    packages = model.get('packages')
    if not isinstance(packages, list) or not packages:
        raise errors.RequestError(msg=f'{model_id}.packages 必须是非空数组')
    references = [
        _parse_release_package(
            package,
            model_id=model_id,
            model_version=model_version,
            license_name=license_name.strip(),
            license_url=license_url,
            source_url=source_url,
        )
        for package in packages
    ]
    platform_keys = {(item.os, item.arch, item.acceleration) for item in references}
    if len(platform_keys) != len(references):
        raise errors.RequestError(msg=f'{model_id} 含重复平台包')
    return (model_id, model_version), references


def parse_release_manifest(catalog_json: str) -> SpeechCatalogReleaseManifest:
    """严格解析 v2 发布信封并收集所有签名包引用。

    云端仍不持有 Ed25519 公钥、也不替代 daemon 验签；这里仅做原子发布所需的结构、
    内容寻址、许可证和全引用一致性预检。
    """
    try:
        document = json.loads(catalog_json)
    except (TypeError, ValueError) as exc:
        raise errors.RequestError(msg=f'catalog 不是合法 JSON: {exc}') from exc
    if not isinstance(document, dict) or set(document) != _V2_ENVELOPE_FIELDS:
        raise errors.RequestError(msg='catalog 必须使用完整 v2 发布信封')

    key_id = _require_identifier(document['key_id'], field='key_id')
    release_sequence = _require_positive_int(document['release_sequence'], field='release_sequence')
    expires_at = _parse_rfc3339(document['expires_at'], field='expires_at')
    signature = document['signature']
    if not isinstance(signature, str) or _SIGNATURE_PATTERN.fullmatch(signature) is None:
        raise errors.RequestError(msg='catalog signature 必须是 128 位规范小写十六进制')

    payload = document['payload']
    if not isinstance(payload, dict):
        raise errors.RequestError(msg='catalog.payload 必须是对象')
    catalog_version = _require_identifier(payload.get('catalog_version'), field='catalog_version')
    issued_at = _parse_rfc3339(payload.get('issued_at'), field='issued_at')
    if expires_at <= issued_at:
        raise errors.RequestError(msg='expires_at 必须晚于 issued_at')
    models = payload.get('models')
    if not isinstance(models, list) or not models:
        raise errors.RequestError(msg='catalog.payload.models 必须是非空数组')

    references: list[SpeechCatalogReleasePackageReference] = []
    model_keys: set[tuple[str, str]] = set()
    for model in models:
        model_key, model_references = _parse_release_model(
            model,
            release_sequence=release_sequence,
            release_expires_at=document['expires_at'],
        )
        if model_key in model_keys:
            raise errors.RequestError(msg=f'catalog model 重复: {model_key[0]}/{model_key[1]}')
        model_keys.add(model_key)
        references.extend(model_references)

    return SpeechCatalogReleaseManifest(
        key_id=key_id,
        release_sequence=release_sequence,
        expires_at=expires_at,
        catalog_version=catalog_version,
        issued_at=issued_at,
        payload=payload,
        packages=tuple(references),
    )


def validate_staged_release_packages(
    release: SpeechCatalogReleaseManifest,
    staged_by_sha256: dict[str, StagedSpeechPackageEvidence],
) -> tuple[StagedSpeechPackageEvidence, ...]:
    """证明 release 引用的每个唯一对象都已暂存且元数据逐项一致。"""
    validated: list[StagedSpeechPackageEvidence] = []
    seen: set[str] = set()
    for package in release.packages:
        staged = staged_by_sha256.get(package.sha256)
        if staged is None:
            raise errors.RequestError(msg=f'模型包尚未暂存: {package.sha256}')
        if staged.sha256 != package.sha256:
            raise errors.RequestError(msg=f'模型包 sha256 登记不一致: {package.sha256}')
        expected_key = build_speech_package_object_key(package.sha256)
        if staged.object_key != expected_key:
            raise errors.RequestError(msg=f'模型包对象 key 不符合内容寻址规则: {package.sha256}')
        if staged.stable_url.rstrip('/') != package.url.rstrip('/'):
            raise errors.RequestError(msg=f'模型包 URL 与暂存对象不一致: {package.sha256}')
        if staged.size != package.compressed_size:
            raise errors.RequestError(msg=f'模型包大小与 catalog 声明不一致: {package.sha256}')
        if package.sha256 not in seen:
            seen.add(package.sha256)
            validated.append(staged)
    return tuple(validated)


def validate_release_transition(
    *,
    current_sequence: int | None,
    current_revision: str | None,
    candidate_sequence: int,
    candidate_revision: str,
) -> Literal['publish', 'idempotent']:
    """执行全局单调序列和同内容幂等判定。"""
    if current_sequence is None:
        return 'publish'
    if candidate_sequence < current_sequence:
        raise errors.ConflictError(msg=f'发布序列回退：当前 {current_sequence}，候选 {candidate_sequence}')
    if candidate_sequence == current_sequence:
        if candidate_revision == current_revision:
            return 'idempotent'
        raise errors.ConflictError(msg=f'发布序列 {candidate_sequence} 已被不同 catalog 占用，发生冲突')
    return 'publish'


def compute_revision(catalog_json: str) -> str:
    """catalog 原文指纹：sha256(catalog_json 原文)[:16]。

    对 catalog 逐字节原文取指纹——内容变（含重签、换版本）→ 指纹变 → daemon 重拉。
    """
    return hashlib.sha256(catalog_json.encode('utf-8')).hexdigest()[:16]


def _validate_object_stat(
    *,
    sha256: str,
    registered_size: int,
    registered_etag: str | None,
    actual_size: int,
    actual_etag: str | None,
    allow_etag_backfill: bool,
) -> str:
    """校验对象元数据，并返回可写入登记的不可变版本标识。"""
    if actual_size != registered_size:
        raise errors.ServerError(
            msg=f'语音包登记大小与真实对象不一致: {sha256}，登记 {registered_size}，实际 {actual_size}'
        )
    if not actual_etag:
        raise errors.ServerError(msg=f'语音包对象存储未返回不可变版本标识: {sha256}')
    if registered_etag is None:
        if allow_etag_backfill:
            return actual_etag
        raise errors.ServerError(msg=f'语音包缺少对象版本证据，必须重新暂存: {sha256}')
    if registered_etag != actual_etag:
        raise errors.ServerError(msg=f'语音包对象版本已变化，拒绝发布: {sha256}')
    return registered_etag


def _build_summary(payload: dict[str, Any]) -> list[SpeechCatalogModelSummary]:
    """从 catalog.payload 抽模型摘要（管理端展示，非权威）。"""
    summaries: list[SpeechCatalogModelSummary] = []
    for model in payload.get('models', []):
        if not isinstance(model, dict):
            continue
        platforms: list[str] = []
        for pkg in model.get('packages', []) or []:
            if not isinstance(pkg, dict):
                continue
            plat = pkg.get('platform') or {}
            if isinstance(plat, dict):
                platforms.append(f'{plat.get("os", "")}-{plat.get("arch", "")}-{plat.get("acceleration", "")}')
        summaries.append(
            SpeechCatalogModelSummary(
                model_id=str(model.get('model_id', '')),
                model_version=str(model.get('model_version', '')),
                display_name=str(model.get('display_name', '')),
                engine=str(model.get('engine', '')),
                platforms=platforms,
                package_count=len(model.get('packages', []) or []),
            )
        )
    return summaries


class SpeechCatalogService:
    """通用语音模型签名目录读写（云端权威单行）。"""

    @staticmethod
    async def _get_row(db: AsyncSession, *, for_update: bool = False) -> HasnSpeechCatalog | None:
        statement = sa.select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == _CONFIG_KEY).limit(1)
        if for_update:
            statement = statement.with_for_update()
        return (await db.execute(statement)).scalar_one_or_none()

    @staticmethod
    async def _package_response(
        db: AsyncSession,
        package: HasnSpeechPackage,
        *,
        already_exists: bool,
        allow_etag_backfill: bool = False,
    ) -> tuple[SpeechPackageStageResponse, StagedSpeechPackageEvidence]:
        """复核登记行、公开直链和真实对象元数据，并构造稳定出参。"""
        expected_key = build_speech_package_object_key(package.sha256)
        if package.object_key != expected_key:
            raise errors.ServerError(msg=f'语音包登记对象 key 与摘要不一致: {package.sha256}')
        storage = await StorageService.get_storage(db, package.storage_id)
        if getattr(storage, 'access', 'private') != 'public':
            raise errors.ServerError(msg=f'语音包 {package.sha256} 未存入公共存储')
        stable_url = StorageService.public_url(storage, package.object_key)
        if not stable_url.startswith('https://'):
            raise errors.ServerError(msg=f'公开桶 CDN 非 https，桌面端 ATS 会拒下: {stable_url}')
        stat = await StorageService.stat(
            db,
            storage_id=package.storage_id,
            object_key=package.object_key,
        )
        package.object_etag = _validate_object_stat(
            sha256=package.sha256,
            registered_size=package.size,
            registered_etag=package.object_etag,
            actual_size=stat.size,
            actual_etag=stat.etag,
            allow_etag_backfill=allow_etag_backfill,
        )
        actual_sha256, hashed_size = await StorageService.sha256(
            db,
            storage_id=package.storage_id,
            object_key=package.object_key,
        )
        if hashed_size != stat.size:
            raise errors.ServerError(
                msg=f'语音包流式读取大小与对象元数据不一致: {package.sha256}，元数据 {stat.size}，读取 {hashed_size}'
            )
        if actual_sha256 != package.sha256:
            raise errors.ServerError(msg=f'语音包真实对象 SHA-256 与登记不一致: {package.sha256}，实际 {actual_sha256}')
        response = SpeechPackageStageResponse(
            package_id=package.id,
            sha256=package.sha256,
            object_key=package.object_key,
            download_url=stable_url,
            size=stat.size,
            already_exists=already_exists,
        )
        return response, StagedSpeechPackageEvidence(
            sha256=package.sha256,
            object_key=package.object_key,
            stable_url=stable_url,
            size=stat.size,
        )

    async def _register_uploaded_package(
        self,
        db: AsyncSession,
        *,
        digest: str,
        size: int,
        uploaded: ObjectRef,
        storage: S3Storage,
    ) -> SpeechPackageStageResponse:
        """锁外复核真实对象，锁内复核存储快照并登记内容寻址包。"""
        object_key = build_speech_package_object_key(digest)
        if uploaded.object_key != object_key or uploaded.size != size or uploaded.storage_id != storage.id:
            raise errors.ServerError(msg='对象存储上传结果与语音包内容寻址元数据不一致')
        if not uploaded.stable_url.startswith('https://'):
            raise errors.ServerError(msg=f'公开桶 CDN 非 https，桌面端 ATS 会拒下: {uploaded.stable_url}')
        stat_size, object_etag = await stat_object(storage, uploaded.object_key)
        if stat_size != size or not object_etag:
            raise errors.ServerError(msg='语音包上传后的对象大小或版本证据无效')
        actual_sha256, hashed_size = await sha256_object(storage, uploaded.object_key)
        if actual_sha256 != digest or hashed_size != size:
            raise errors.ServerError(msg='语音包上传后的真实对象 SHA-256 或大小不一致')

        if db.in_transaction():
            await db.rollback()
        async with db.begin():
            await db.execute(
                sa.text('SELECT pg_advisory_xact_lock(:lock_key)'),
                {'lock_key': SPEECH_STORAGE_LOCK_KEY},
            )
            current_storage = await db.scalar(sa.select(S3Storage).where(S3Storage.id == storage.id).with_for_update())
            if current_storage is None or _storage_snapshot(current_storage) != _storage_snapshot(storage):
                raise errors.ConflictError(msg='语音包存储配置在暂存登记前发生变化，请重新暂存')

            insert_statement = (
                pg_insert(HasnSpeechPackage)
                .values(
                    sha256=digest,
                    storage_id=uploaded.storage_id,
                    object_key=object_key,
                    size=size,
                    object_etag=object_etag,
                    content_type='application/zip',
                )
                .on_conflict_do_nothing(index_elements=[HasnSpeechPackage.sha256])
                .returning(HasnSpeechPackage.id)
            )
            inserted_id = (await db.execute(insert_statement)).scalar_one_or_none()
            package = await db.scalar(sa.select(HasnSpeechPackage).where(HasnSpeechPackage.sha256 == digest).limit(1))
            if package is None:
                raise errors.ServerError(msg=f'语音包上传后未能完成内容寻址登记: {digest}')
            expected_package = (
                uploaded.storage_id,
                object_key,
                size,
                object_etag,
            )
            actual_package = (
                package.storage_id,
                package.object_key,
                package.size,
                package.object_etag,
            )
            if actual_package != expected_package:
                raise errors.ConflictError(msg=f'同摘要语音包已有不同不可变登记: {digest}')
            return SpeechPackageStageResponse(
                package_id=package.id,
                sha256=digest,
                object_key=object_key,
                download_url=uploaded.stable_url,
                size=size,
                already_exists=inserted_id is None,
            )

    async def stage_package_upload(
        self,
        db: AsyncSession,
        *,
        upload: UploadFile,
    ) -> SpeechPackageStageResponse:
        """两遍流式处理 UploadFile：先哈希/校验 ZIP，再按摘要 key 有界上传。"""
        if upload.content_type not in {
            None,
            '',
            'application/zip',
            'application/x-zip-compressed',
        }:
            raise errors.RequestError(msg='模型包 content_type 必须是 application/zip')

        digest = hashlib.sha256()
        size = 0
        await upload.seek(0)
        while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_SPEECH_PACKAGE_BYTES:
                raise errors.RequestError(msg=f'模型包超过大小上限 {MAX_SPEECH_PACKAGE_BYTES} 字节')
            digest.update(chunk)
        if size == 0:
            raise errors.RequestError(msg='模型 zip 不能为空')

        await upload.seek(0)
        if not zipfile.is_zipfile(upload.file):
            raise errors.RequestError(msg='模型包必须是可读取的 ZIP 文件')
        package_sha256 = digest.hexdigest()
        existing = await db.scalar(
            sa.select(HasnSpeechPackage).where(HasnSpeechPackage.sha256 == package_sha256).limit(1)
        )
        if existing is not None:
            response, _ = await self._package_response(
                db,
                existing,
                already_exists=True,
                allow_etag_backfill=True,
            )
            await db.commit()
            return response

        storage_row = await StorageService.get_immutable_speech_storage(db)
        storage = _copy_storage(storage_row)
        await db.rollback()
        object_key = build_speech_package_object_key(package_sha256)
        await upload.seek(0)
        uploaded = await StorageService.upload_immutable_speech_package_to_storage(
            storage,
            upload.file,
            size=size,
            content_type='application/zip',
            key=object_key,
        )
        return await self._register_uploaded_package(
            db,
            digest=package_sha256,
            size=size,
            uploaded=uploaded,
            storage=storage,
        )

    async def get_node_response(self, db: AsyncSession) -> SpeechCatalogNodeResponse:
        """节点拉取签名 catalog + revision（无行时返回空——daemon 保持未装配态，零 fake）。"""
        row = await self._get_row(db)
        if row is None or not row.catalog_json:
            return SpeechCatalogNodeResponse(catalog_json=None, revision='', catalog_version='')
        return SpeechCatalogNodeResponse(
            catalog_json=row.catalog_json,
            revision=row.revision,
            catalog_version=row.catalog_version,
            published_time=row.updated_time or row.created_time,
        )

    async def get_revision(self, db: AsyncSession) -> str:
        """当前已发布 catalog 的 revision（无行时空串）——供 sync_invalidate 全局指纹。"""
        row = await self._get_row(db)
        return row.revision if (row and row.catalog_json) else ''

    @staticmethod
    async def _load_package_verification_inputs(
        db: AsyncSession,
        digests: set[str],
    ) -> list[_PackageVerificationInput]:
        """复制发布所需登记与存储配置，并在返回前释放数据库事务。"""
        package_rows = (
            (await db.execute(sa.select(HasnSpeechPackage).where(HasnSpeechPackage.sha256.in_(digests))))
            .scalars()
            .all()
        )
        storage_ids = {package.storage_id for package in package_rows}
        storage_rows = (await db.execute(sa.select(S3Storage).where(S3Storage.id.in_(storage_ids)))).scalars().all()
        storages: dict[int, S3Storage] = {}
        for storage_row in storage_rows:
            storages[storage_row.id] = S3Storage(
                name=storage_row.name,
                endpoint=storage_row.endpoint,
                access_key=storage_row.access_key,
                secret_key=storage_row.secret_key,
                bucket=storage_row.bucket,
                access=storage_row.access,
                sign_strategy=storage_row.sign_strategy,
                prefix=storage_row.prefix,
                region=storage_row.region,
                cdn_domain=storage_row.cdn_domain,
                remark=storage_row.remark,
            )
        verification_inputs: list[_PackageVerificationInput] = []
        for package in package_rows:
            package_storage = storages.get(package.storage_id)
            if package_storage is None:
                raise errors.ServerError(msg=f'S3 存储 {package.storage_id} 不存在')
            verification_inputs.append(
                _PackageVerificationInput(
                    package_id=package.id,
                    sha256=package.sha256,
                    storage_id=package.storage_id,
                    object_key=package.object_key,
                    size=package.size,
                    object_etag=package.object_etag,
                    storage=package_storage,
                )
            )
        # SQLAlchemy 的首次 SELECT 会自动开启事务。远程模型包完整 GET 之前必须回滚，
        # 避免长时间占用 PostgreSQL 连接，更不能持有发布锁或包行锁。
        await db.rollback()
        return verification_inputs

    @staticmethod
    async def _verify_package_without_database(
        package: _PackageVerificationInput,
    ) -> tuple[_PackageSnapshot, SpeechPackageStageResponse, StagedSpeechPackageEvidence]:
        """不持有数据库连接，复核一个真实对象并生成发布证据。"""
        expected_key = build_speech_package_object_key(package.sha256)
        if package.object_key != expected_key:
            raise errors.ServerError(msg=f'语音包登记对象 key 与摘要不一致: {package.sha256}')
        if package.storage.access != 'public':
            raise errors.ServerError(msg=f'语音包 {package.sha256} 未存入公共存储')
        stable_url = build_object_url(package.storage, package.object_key)
        if not stable_url.startswith('https://'):
            raise errors.ServerError(msg=f'公开桶 CDN 非 https，桌面端 ATS 会拒下: {stable_url}')
        stat_size, stat_etag = await stat_object(package.storage, package.object_key)
        object_etag = _validate_object_stat(
            sha256=package.sha256,
            registered_size=package.size,
            registered_etag=package.object_etag,
            actual_size=stat_size,
            actual_etag=stat_etag,
            allow_etag_backfill=False,
        )
        actual_sha256, hashed_size = await sha256_object(package.storage, package.object_key)
        if hashed_size != stat_size:
            raise errors.ServerError(
                msg=(f'语音包流式读取大小与对象元数据不一致: {package.sha256}，元数据 {stat_size}，读取 {hashed_size}')
            )
        if actual_sha256 != package.sha256:
            raise errors.ServerError(msg=f'语音包真实对象 SHA-256 与登记不一致: {package.sha256}，实际 {actual_sha256}')
        snapshot = (
            package.package_id,
            package.storage_id,
            package.object_key,
            package.size,
            object_etag,
            _storage_snapshot(package.storage),
        )
        response = SpeechPackageStageResponse(
            package_id=package.package_id,
            sha256=package.sha256,
            object_key=package.object_key,
            download_url=stable_url,
            size=stat_size,
            already_exists=True,
        )
        evidence = StagedSpeechPackageEvidence(
            sha256=package.sha256,
            object_key=package.object_key,
            stable_url=stable_url,
            size=stat_size,
        )
        return snapshot, response, evidence

    async def _verify_release_packages_before_transaction(
        self,
        db: AsyncSession,
        release: SpeechCatalogReleaseManifest,
    ) -> tuple[
        dict[str, _PackageSnapshot],
        dict[str, SpeechPackageStageResponse],
        tuple[StagedSpeechPackageEvidence, ...],
    ]:
        """释放数据库连接后，完成真实对象 GET、SHA-256、大小、URL 和版本证据复核。"""
        digests = {item.sha256 for item in release.packages}
        verification_inputs = await self._load_package_verification_inputs(db, digests)

        snapshots: dict[str, _PackageSnapshot] = {}
        responses: dict[str, SpeechPackageStageResponse] = {}
        evidence: dict[str, StagedSpeechPackageEvidence] = {}
        for package in verification_inputs:
            snapshot, response, package_evidence = await self._verify_package_without_database(package)
            snapshots[package.sha256] = snapshot
            responses[package.sha256] = response
            evidence[package.sha256] = package_evidence
        validated = validate_staged_release_packages(release, evidence)
        return snapshots, responses, validated

    @staticmethod
    async def _lock_verified_package_rows(
        db: AsyncSession,
        snapshots: dict[str, _PackageSnapshot],
    ) -> dict[str, HasnSpeechPackage]:
        """锁内只复核不可变登记快照，禁止再次执行远程对象 I/O。"""
        rows = (
            (
                await db.execute(
                    sa.select(HasnSpeechPackage).where(HasnSpeechPackage.sha256.in_(snapshots)).with_for_update()
                )
            )
            .scalars()
            .all()
        )
        packages = {item.sha256: item for item in rows}
        storage_ids = {expected[1] for expected in snapshots.values()}
        storage_rows = (
            (await db.execute(sa.select(S3Storage).where(S3Storage.id.in_(storage_ids)).with_for_update()))
            .scalars()
            .all()
        )
        storages = {item.id: item for item in storage_rows}
        for digest, expected in snapshots.items():
            package = packages.get(digest)
            if package is None:
                raise errors.ConflictError(msg=f'语音包在发布切换前被删除: {digest}')
            actual = (
                package.id,
                package.storage_id,
                package.object_key,
                package.size,
                package.object_etag or '',
            )
            if actual != expected[:5]:
                raise errors.ConflictError(msg=f'语音包在发布切换前发生变化: {digest}')
            storage = storages.get(package.storage_id)
            if storage is None or _storage_snapshot(storage) != expected[5]:
                raise errors.ConflictError(msg=f'语音包存储配置在发布切换前发生变化: {digest}')
        return packages

    async def publish_release(
        self,
        db: AsyncSession,
        *,
        catalog_json: str,
        published_by: str | None,
    ) -> SpeechCatalogPublishResponse:
        """锁外复核大对象，随后在短事务内写不可变 release、映射和当前 head。"""
        catalog_json = catalog_json or ''
        if not catalog_json:
            raise errors.RequestError(msg='catalog 不能为空')
        release = parse_release_manifest(catalog_json)
        revision = compute_revision(catalog_json)
        summary = _build_summary(release.payload)
        summary_json = [s.model_dump(mode='json') for s in summary]

        try:
            snapshots, responses_by_sha256, validated = await self._verify_release_packages_before_transaction(
                db, release
            )
        except Exception:
            if db.in_transaction():
                await db.rollback()
            raise
        if db.in_transaction():
            await db.rollback()

        async with db.begin():
            # 锁内只做 PostgreSQL 快速复核和权威切换，不执行 S3 GET。
            await db.execute(
                sa.text('SELECT pg_advisory_xact_lock(:lock_key)'),
                {'lock_key': _RELEASE_LOCK_KEY},
            )
            head = await self._get_row(db, for_update=True)
            packages_by_sha256 = await self._lock_verified_package_rows(db, snapshots)

            current_sequence = (
                int(head.release_sequence) if head is not None and head.release_sequence is not None else None
            )
            transition = validate_release_transition(
                current_sequence=current_sequence,
                current_revision=head.revision if head is not None else None,
                candidate_sequence=release.release_sequence,
                candidate_revision=revision,
            )
            if transition == 'idempotent':
                if head is None or head.current_release_id is None:
                    raise errors.ServerError(msg='语音目录 head 命中幂等判定但缺少不可变 release 指针')
                existing_release = await db.get(
                    HasnSpeechCatalogRelease,
                    head.current_release_id,
                )
                if existing_release is None or existing_release.revision != revision:
                    raise errors.ServerError(msg='语音目录 head 与不可变 release 账本不一致')
                return SpeechCatalogPublishResponse(
                    release_id=existing_release.id,
                    revision=revision,
                    release_sequence=release.release_sequence,
                    key_id=release.key_id,
                    catalog_version=release.catalog_version,
                    idempotent=True,
                    packages=[responses_by_sha256[item.sha256] for item in validated],
                    models=summary,
                )

            release_row = HasnSpeechCatalogRelease(
                revision=revision,
                release_sequence=Decimal(release.release_sequence),
                key_id=release.key_id,
                catalog_version=release.catalog_version,
                expires_at=release.expires_at,
                catalog_json=catalog_json,
                model_summary=summary_json,
                published_by=published_by,
            )
            db.add(release_row)
            await db.flush()

            for reference in release.packages:
                package = packages_by_sha256[reference.sha256]
                db.add(
                    HasnSpeechCatalogReleasePackage(
                        release_id=release_row.id,
                        package_id=package.id,
                        model_id=reference.model_id,
                        model_version=reference.model_version,
                        os=reference.os,
                        arch=reference.arch,
                        acceleration=reference.acceleration,
                        installed_size=reference.installed_size,
                        license_name=reference.license_name,
                        license_url=reference.license_url,
                        source_url=reference.source_url,
                    )
                )

            if head is None:
                head = HasnSpeechCatalog(
                    config_key=_CONFIG_KEY,
                    catalog_json=catalog_json,
                    revision=revision,
                    catalog_version=release.catalog_version,
                    current_release_id=release_row.id,
                    release_sequence=Decimal(release.release_sequence),
                    key_id=release.key_id,
                    model_summary=summary_json,
                    published_by=published_by,
                )
                db.add(head)
            else:
                head.catalog_json = catalog_json
                head.revision = revision
                head.catalog_version = release.catalog_version
                head.current_release_id = release_row.id
                head.release_sequence = Decimal(release.release_sequence)
                head.key_id = release.key_id
                head.model_summary = summary_json
                head.published_by = published_by
            await db.flush()

            return SpeechCatalogPublishResponse(
                release_id=release_row.id,
                revision=revision,
                release_sequence=release.release_sequence,
                key_id=release.key_id,
                catalog_version=release.catalog_version,
                idempotent=False,
                packages=[responses_by_sha256[item.sha256] for item in validated],
                models=summary,
            )


speech_catalog_service = SpeechCatalogService()
