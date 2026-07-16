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

from typing import TYPE_CHECKING, Any

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
                platforms.append(
                    f'{plat.get("os", "")}-{plat.get("arch", "")}-{plat.get("acceleration", "")}'
                )
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
            await db.execute(
                sa.select(HasnSpeechCatalog).where(HasnSpeechCatalog.config_key == _CONFIG_KEY).limit(1)
            )
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
                msg=f'catalog 无任何平台包 URL 指向 object_key={object_key}（发布方 --package-url 与 object_key 不一致）'
            )

        # 一致性预检 2：本次 zip 的 sha256 必须与指向它的包声明一致（发布方 sha256 与实际文件对拍）。
        actual_sha256 = hashlib.sha256(zip_bytes).hexdigest()
        for pkg in referencing:
            declared = str(pkg.get('sha256', '')).lower()
            if declared and declared != actual_sha256.lower():
                raise errors.RequestError(
                    msg=f'zip sha256 不匹配 catalog 声明：实际 {actual_sha256}，声明 {declared}'
                )

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
