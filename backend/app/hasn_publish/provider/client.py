"""云端业务模块 → Publish 内部 HTTP 的唯一调用接缝。"""

from __future__ import annotations

import logging

from typing import Any

import httpx

from backend.common.exception import errors
from backend.common.response.response_code import StandardResponseCode
from backend.common.service_http import get_service_client
from backend.common.service_registry import service_endpoint

logger = logging.getLogger(__name__)


class PublishProvider:
    """严格调用 Publish 内部接口；未配置、超时或协议异常均显式失败。"""

    async def resolve_form_access(
        self,
        *,
        publish_ref: str,
        form_access_token: str,
    ) -> dict[str, Any]:
        endpoint = service_endpoint('publish')
        if not endpoint.base_url or not endpoint.token:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='Publish 内部服务尚未配置',
                data={'error_code': 'PUBLISH_SERVICE_UNCONFIGURED'},
            )
        try:
            response = await get_service_client('publish').post(
                f'{endpoint.base_url}/api/v1/publish/internal/forms/resolve',
                json={
                    'publish_ref': publish_ref,
                    'form_access_token': form_access_token,
                },
                headers={'X-Internal-Token': endpoint.token},
                timeout=endpoint.timeout,
            )
        except httpx.TimeoutException as exc:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='Publish 内部服务响应超时',
                data={'error_code': 'PUBLISH_SERVICE_TIMEOUT'},
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning('Publish 内部服务不可达: %s', exc.__class__.__name__)
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='Publish 内部服务暂时不可用',
                data={'error_code': 'PUBLISH_SERVICE_UNAVAILABLE'},
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise errors.GatewayError(msg='Publish 内部服务返回了非预期格式') from exc
        message = payload.get('msg') if isinstance(payload, dict) else None
        data = payload.get('data') if isinstance(payload, dict) else None
        if response.status_code == 404:
            raise errors.NotFoundError(msg=message or '落地页不存在或已下线', data=data)
        if response.status_code == 403:
            raise errors.ForbiddenError(msg=message or '表单访问校验失败', data=data)
        if response.status_code == 409:
            raise errors.ConflictError(msg=message or 'Publish 站点状态冲突', data=data)
        if response.status_code == 429:
            raise errors.RequestError(code=429, msg=message or '请求过于频繁', data=data)
        if response.status_code != 200:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='Publish 内部服务暂时不可用',
                data={'error_code': 'PUBLISH_SERVICE_ERROR'},
            )
        if not isinstance(payload, dict) or payload.get('code') != 200 or not isinstance(data, dict):
            raise errors.GatewayError(msg='Publish 内部服务响应契约无效')
        return data

    async def get_growth_site_status(
        self,
        *,
        owner_hasn_id: str,
        platform_project_id: str,
        growth_project_id: str,
    ) -> dict[str, Any] | None:
        """按 Growth 来源读取 Publish 唯一站点；错误语义与表单解析一致保持显式。"""
        endpoint = service_endpoint('publish')
        if not endpoint.base_url or not endpoint.token:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='Publish 内部服务尚未配置',
                data={'error_code': 'PUBLISH_SERVICE_UNCONFIGURED'},
            )
        try:
            response = await get_service_client('publish').post(
                f'{endpoint.base_url}/api/v1/publish/internal/growth/sites/status',
                json={
                    'owner_hasn_id': owner_hasn_id,
                    'platform_project_id': platform_project_id,
                    'growth_project_id': growth_project_id,
                },
                headers={'X-Internal-Token': endpoint.token},
                timeout=endpoint.timeout,
            )
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='Publish 内部服务响应超时',
                data={'error_code': 'PUBLISH_SERVICE_TIMEOUT'},
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning('Publish 站点状态接口不可用: %s', exc.__class__.__name__)
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='Publish 内部服务暂时不可用',
                data={'error_code': 'PUBLISH_SERVICE_UNAVAILABLE'},
            ) from exc
        message = payload.get('msg') if isinstance(payload, dict) else None
        data = payload.get('data') if isinstance(payload, dict) else None
        if response.status_code == 404:
            raise errors.NotFoundError(msg=message or 'Publish 站点不存在', data=data)
        if response.status_code == 403:
            raise errors.ForbiddenError(msg=message or 'Publish 站点访问被拒绝', data=data)
        if response.status_code == 409:
            raise errors.ConflictError(msg=message or 'Publish 站点项目绑定冲突', data=data)
        if response.status_code != 200:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_503,
                msg='Publish 内部服务暂时不可用',
                data={'error_code': 'PUBLISH_SERVICE_ERROR'},
            )
        if not isinstance(payload, dict) or payload.get('code') != 200 or not isinstance(data, dict):
            raise errors.GatewayError(msg='Publish 内部服务响应契约无效')
        site = data.get('site')
        if site is not None and not isinstance(site, dict):
            raise errors.GatewayError(msg='Publish 站点状态响应契约无效')
        return site


publish_provider = PublishProvider()
