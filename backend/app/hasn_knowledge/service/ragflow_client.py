"""RAGFlow 数据面 client（云端内部，knowledge service 唯一调用方）。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §4.2（端点采用旧文 §3.6 实测真值表）。

纪律：
- knowledge service 是唯一 callsite，禁止其它模块直调 RAGFlow；
- service key 只在本 client 内即用，永不出参、不进日志；
- 故障语义零 fake：不可达/5xx/异常体 → knowledge_provider_unreachable；401/403 → knowledge_provider_unauthorized；
  「确无命中」与「检索故障」严格区分（空结果只能来自 code=0 的正常响应）。
"""

from __future__ import annotations

from typing import Any

import httpx

# 超时（秒）：引擎不在线/挂起时尽快失败、给主人友好提示，而非长时间转圈。
# 元数据类操作（建库/删库/列文档/触发解析）走默认；上传/检索是合法耗时操作，读超时单独放宽。
DEFAULT_TIMEOUT = 25.0
# 连接超时单独收短：引擎完全离线（端口拒绝/无人应答）时快速失败，不必等满整个读超时。
CONNECT_TIMEOUT = 10.0
# 文件上传：上传副本可能较大，读超时留余量（连接超时仍短，离线快速失败）。
UPLOAD_TIMEOUT = 60.0
# 检索：向量检索应较快，30s 足够；超时即归一为引擎不可用。
RETRIEVAL_TIMEOUT = 30.0


class KnowledgeProviderError(Exception):
    """处理后端故障（错误码见设计 §6，emitter=cloud）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f'{code}: {message}')


def _provider_unreachable(detail: str) -> KnowledgeProviderError:
    return KnowledgeProviderError('knowledge_provider_unreachable', detail)


def _provider_unauthorized(detail: str) -> KnowledgeProviderError:
    return KnowledgeProviderError('knowledge_provider_unauthorized', detail)


def _build_timeout(total: float) -> httpx.Timeout:
    """构造 httpx 超时：连接超时收短（离线快速失败），读/写/池超时用 total。"""
    return httpx.Timeout(total, connect=min(CONNECT_TIMEOUT, total))


def _map_request_error(exc: httpx.HTTPError) -> KnowledgeProviderError:
    """把底层 httpx 异常归一成对主人友好、不泄露 RAGFlow 实现名的提示。"""
    if isinstance(exc, httpx.ConnectError):
        return _provider_unreachable('无法连接知识库引擎，可能尚未启动，请稍后重试')
    if isinstance(exc, httpx.TimeoutException):
        return _provider_unreachable('知识库引擎响应超时，可能正在繁忙或未就绪，请稍后重试')
    return _provider_unreachable('知识库引擎暂时不可用，请稍后重试')


class RAGFlowClient:
    """RAGFlow HTTP API 数据面封装（/api/v1，Bearer service key）。"""

    def __init__(self, base_url: str, api_key: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip('/')
        self._api_key = api_key
        self.timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self._api_key}'}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            # trust_env=False：RAGFlow 是内网/loopback 服务，服务间调用绝不能走主机 HTTP_PROXY/
            # 系统代理（否则开发机常见的 127.0.0.1:1082 类代理会拦截 loopback 请求并回 503，
            # 既可能误路由，又把「引擎未启动」伪装成误导性的 5xx）。同时不读 .netrc。
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=_build_timeout(timeout or self.timeout), trust_env=False
            ) as client:
                response = await client.request(
                    method, path, json=json, params=params, files=files, headers=self._headers
                )
        except httpx.HTTPError as exc:
            raise _map_request_error(exc) from exc
        if response.status_code in (401, 403):
            raise _provider_unauthorized('RAGFlow service key 无效或权限不足（运营级故障）')
        if response.status_code >= 500:
            raise _provider_unreachable(f'RAGFlow 服务端错误：HTTP {response.status_code}')
        try:
            body = response.json()
        except ValueError as exc:
            raise _provider_unreachable('RAGFlow 返回非 JSON 响应体') from exc
        if not isinstance(body, dict):
            raise _provider_unreachable('RAGFlow 返回异常响应体')
        code = body.get('code')
        if code == 401:
            raise _provider_unauthorized('RAGFlow service key 无效（body code=401）')
        if code != 0:
            raise KnowledgeProviderError(
                'knowledge_provider_error', f'RAGFlow 业务错误（code={code}）：{body.get("message", "")}'
            )
        return body

    async def _request_bytes(self, method: str, path: str) -> bytes:
        try:
            # trust_env=False：同上，内网服务调用不走主机/系统代理（见 _request 注释）。
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=_build_timeout(self.timeout), trust_env=False
            ) as client:
                response = await client.request(method, path, headers=self._headers)
        except httpx.HTTPError as exc:
            raise _map_request_error(exc) from exc
        if response.status_code in (401, 403):
            raise _provider_unauthorized('RAGFlow service key 无效或权限不足')
        if response.status_code >= 400:
            raise _provider_unreachable(f'RAGFlow 下载失败：HTTP {response.status_code}')
        return response.content

    # ---- datasets ----

    async def create_dataset(self, *, name: str, embedding_model: str) -> dict[str, Any]:
        """建库；body 直带 embedding_model（`模型@工厂`），不依赖租户全局默认。"""
        body = await self._request('POST', '/api/v1/datasets', json={'name': name, 'embedding_model': embedding_model})
        return body['data']

    async def delete_datasets(self, *, ids: list[str]) -> None:
        await self._request('DELETE', '/api/v1/datasets', json={'ids': ids})

    # ---- documents ----

    async def upload_document(self, *, dataset_id: str, filename: str, data: bytes, mime: str) -> dict[str, Any]:
        """上传文档副本（multipart 字段 `file`）；返回首个 document 信息。"""
        body = await self._request(
            'POST',
            f'/api/v1/datasets/{dataset_id}/documents',
            files={'file': (filename, data, mime or 'application/octet-stream')},
            timeout=UPLOAD_TIMEOUT,
        )
        docs = body.get('data') or []
        if not docs:
            raise _provider_unreachable('RAGFlow 上传响应缺少 document 数据')
        return docs[0]

    async def list_documents(
        self, *, dataset_id: str, document_id: str | None = None, page: int = 1, page_size: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {'page': page, 'page_size': page_size}
        if document_id:
            params['id'] = document_id
        body = await self._request('GET', f'/api/v1/datasets/{dataset_id}/documents', params=params)
        data = body.get('data') or {}
        return data.get('docs') or []

    async def delete_documents(self, *, dataset_id: str, ids: list[str]) -> None:
        await self._request('DELETE', f'/api/v1/datasets/{dataset_id}/documents', json={'ids': ids})

    async def trigger_parse(self, *, dataset_id: str, document_ids: list[str]) -> None:
        await self._request('POST', f'/api/v1/datasets/{dataset_id}/chunks', json={'document_ids': document_ids})

    async def list_chunks(self, *, dataset_id: str, document_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        """列出文档分块（解析后文本；fetch_doc 给 Agent 回文本用）。"""
        body = await self._request(
            'GET',
            f'/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks',
            params={'page': 1, 'page_size': page_size},
        )
        data = body.get('data') or {}
        return data.get('chunks') or []

    async def download_document(self, *, dataset_id: str, document_id: str) -> bytes:
        """二进制透传（备援；file 原件常规下载走平台私有桶，D10）。"""
        return await self._request_bytes('GET', f'/api/v1/datasets/{dataset_id}/documents/{document_id}')

    # ---- retrieval ----

    async def retrieval(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        top_k: int = 8,
        similarity_threshold: float | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """检索；dataset_ids 恒由 service 显式注入（单点隔离纪律）。"""
        payload: dict[str, Any] = {
            'question': question,
            'dataset_ids': dataset_ids,
            'top_k': top_k,
            'page_size': page_size or top_k,
        }
        if similarity_threshold is not None:
            payload['similarity_threshold'] = similarity_threshold
        body = await self._request('POST', '/api/v1/retrieval', json=payload, timeout=RETRIEVAL_TIMEOUT)
        return body.get('data') or {}
