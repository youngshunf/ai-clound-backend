"""通用语音模型签名目录服务（云端权威·哑存储·单行下发）SPCAT-4。

职责：
  - get_node_response：节点拉取签名 catalog + revision（无行时返回空，daemon 保持未装配）。
  - publish：CI 发布——校验签名 catalog 与 zip 一致后，zip 落公开桶 + catalog 原文入库 + bump revision。

安全模型（同 hasn_release minisign 哲学）：发布方离线 Ed25519 私钥签名，云端**只哑存储 + 下发**，
不验签、不改写。daemon 持内置公钥自行验签才是安全执行点。故：
  - catalog_json 存**逐字节原文**（不解析后重序列化）；daemon verify 会 serde 反序列化 payload
    重算签名，任何字段增删/JSON 归一都会破坏验签。
  - 云端仅做「一致性预检」（zip sha256 与 catalog 声明相符、URL 与落桶直链相符、https），
    早暴露发布方失误，**不代替** daemon 验签。
"""

from __future__ import annotations

import hashlib
import json
import re

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import sqlalchemy as sa

from backend.app.hasn.model.hasn_speech_catalog import HasnSpeechCatalog
from backend.app.hasn.schema.hasn_speech_catalog import (
    SpeechCatalogModelSummary,
    SpeechCatalogNodeResponse,
    SpeechCatalogPublishResponse,
)
from backend.common.exception import errors
from backend.plugin.s3.service.storage_service import StorageService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 单行权威键
_CONFIG_KEY = 'global'
# 模型 zip 落公开桶的类别（storage_service CATEGORY_POLICY：public·不签名·长效 https）
_STORAGE_CATEGORY = 'speech_model'
_SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')
_SIGNATURE_PATTERN = re.compile(r'^[0-9a-f]{128}$')
_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_V2_ENVELOPE_FIELDS = {'payload', 'key_id', 'release_sequence', 'expires_at', 'signature'}


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


def _parse_catalog(catalog_json: str) -> dict[str, Any]:
    """把签名 catalog 原文解析成 dict（仅供一致性预检 + 摘要，非权威）。"""
    try:
        parsed = json.loads(catalog_json)
    except (ValueError, TypeError) as exc:
        raise errors.RequestError(msg=f'catalog 不是合法 JSON: {exc}') from exc
    if not isinstance(parsed, dict) or 'payload' not in parsed or 'signature' not in parsed:
        raise errors.RequestError(msg='catalog 结构非法：应为 {payload, signature}')
    payload = parsed.get('payload')
    if not isinstance(payload, dict) or not isinstance(payload.get('models'), list):
        raise errors.RequestError(msg='catalog.payload.models 缺失或非法')
    return parsed


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


def _packages_referencing(payload: dict[str, Any], object_key: str) -> list[dict[str, Any]]:
    """找出 catalog 内 URL 指向本次上传 object_key 的所有平台包（url path 以 object_key 结尾）。"""
    key = object_key.lstrip('/')
    hits: list[dict[str, Any]] = []
    for model in payload.get('models', []):
        if not isinstance(model, dict):
            continue
        for pkg in model.get('packages', []) or []:
            if not isinstance(pkg, dict):
                continue
            url = str(pkg.get('url', ''))
            # 只按 path 结尾判断，容忍 CDN 域名/查询串差异（URL 权威在签名 catalog 内）
            if url.split('?', 1)[0].rstrip('/').endswith(key):
                hits.append(pkg)
    return hits


class SpeechCatalogService:
    """通用语音模型签名目录读写（云端权威单行）。"""

    @staticmethod
    async def _get_row(db: AsyncSession) -> HasnSpeechCatalog | None:
        return (
            await db.execute(sa.select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == _CONFIG_KEY).limit(1))
        ).scalar_one_or_none()

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

    async def publish(
        self,
        db: AsyncSession,
        *,
        catalog_json: str,
        zip_bytes: bytes,
        object_key: str,
        published_by: str | None,
    ) -> SpeechCatalogPublishResponse:
        """CI 发布签名 catalog + 模型 zip（哑存储：只做一致性预检，不代替 daemon 验签）。

        顺序铁律（同 film_engine）：**先把 zip 传公开桶可达、再落 catalog**，绝不让 daemon 去下 404。
        不在此 commit（API 经 CurrentSessionTransaction 自动提交），仅 flush。
        """
        catalog_json = (catalog_json or '').strip()
        object_key = (object_key or '').strip().lstrip('/')
        if not catalog_json:
            raise errors.RequestError(msg='catalog 不能为空')
        if not zip_bytes:
            raise errors.RequestError(msg='模型 zip 不能为空')
        if not object_key:
            raise errors.RequestError(msg='object_key 不能为空')

        parsed = _parse_catalog(catalog_json)
        payload = parsed['payload']

        # 一致性预检 1：catalog 必须有一个平台包 URL 指向本次上传的 object_key。
        referencing = _packages_referencing(payload, object_key)
        if not referencing:
            raise errors.RequestError(
                msg=(
                    f'catalog 无任何平台包 URL 指向 object_key={object_key}'
                    '（发布方 --package-url 与 object_key 不一致）'
                )
            )

        # 一致性预检 2：本次 zip 的 sha256 必须与指向它的包声明一致（发布方 sha256 与实际文件对拍）。
        actual_sha256 = hashlib.sha256(zip_bytes).hexdigest()
        for pkg in referencing:
            declared = str(pkg.get('sha256', '')).lower()
            if declared and declared != actual_sha256.lower():
                raise errors.RequestError(msg=f'zip sha256 不匹配 catalog 声明：实际 {actual_sha256}，声明 {declared}')

        # 落公开桶（category=speech_model → public·不签名·长效直链）。
        ref = await StorageService.upload(
            db,
            zip_bytes,
            category=_STORAGE_CATEGORY,
            filename=object_key.rsplit('/', 1)[-1] or 'model.zip',
            content_type='application/zip',
            key=object_key,
        )
        # 一致性预检 3：桌面端走 ATS，公开桶必须 https（http 直链会被拒下）。
        if not ref.stable_url.startswith('https://'):
            raise errors.ServerError(msg=f'公开桶 CDN 非 https，桌面端 ATS 会拒下: {ref.stable_url}')

        # 一致性预检 4：落桶直链须与 catalog 内嵌 URL 一致（否则 daemon 会去下错地址）。
        matched_url = any(
            str(pkg.get('url', '')).split('?', 1)[0].rstrip('/') == ref.stable_url.split('?', 1)[0].rstrip('/')
            for pkg in referencing
        )
        if not matched_url:
            declared_urls = sorted({str(pkg.get('url', '')) for pkg in referencing})
            raise errors.RequestError(
                msg=(
                    f'落桶直链 {ref.stable_url} 与 catalog 声明 URL 不一致：{declared_urls}。'
                    '发布方须用公开桶权威直链作 --package-url 再签名'
                )
            )

        # 落 catalog 原文 + revision（覆盖式单行；首次即建行）。
        revision = compute_revision(catalog_json)
        catalog_version = str(payload.get('catalog_version', ''))[:64]
        summary = _build_summary(payload)
        summary_json = [s.model_dump(mode='json') for s in summary]
        row = await self._get_row(db)
        if row is None:
            row = HasnSpeechCatalog(
                config_key=_CONFIG_KEY,
                catalog_json=catalog_json,
                revision=revision,
                catalog_version=catalog_version,
                model_summary=summary_json,
                published_by=published_by,
            )
            db.add(row)
        else:
            row.catalog_json = catalog_json
            row.revision = revision
            row.catalog_version = catalog_version
            row.model_summary = summary_json
            row.published_by = published_by
        await db.flush()

        return SpeechCatalogPublishResponse(
            revision=revision,
            catalog_version=catalog_version,
            object_key=ref.object_key,
            download_url=ref.stable_url,
            size=ref.size,
            sha256=actual_sha256,
            models=summary,
        )


speech_catalog_service = SpeechCatalogService()
