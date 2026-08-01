"""图坊模型两阶段发布端点的进程内 HTTP E2E。

本仓硬规则「加端点要跑真实 HTTP」：service 层测试绕过 HTTP 栈，抓不到统一信封漂移、
multipart/Form 绑定错误、路由依赖装配错误这一层。这里挂真实 router，经 ASGITransport
走完整 FastAPI 栈（multipart 解析 + 依赖注入 + `{code,msg,data}` 信封）。

不依赖真实 PG / 对象存储：DB 会话与存储上传均以桩替换，被测的是外壳与守卫，
内容正确性由同目录的 service 层契约测试覆盖。
"""

from __future__ import annotations

import io
import zipfile

from typing import Any

import httpx
import pytest

from fastapi import FastAPI
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.admin.hasn_app_catalog import router as admin_catalog_router
from backend.app.hasn.service import app_catalog_service
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.rbac import rbac_verify
from backend.database.db import get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(admin_catalog_router, prefix='/api/v1/hasn/app-catalogs')
# 业务异常要经真实处理器落成信封化的 4xx，而不是冒泡成 500；
# ContextMiddleware 是这些处理器取 trace_id 的前提。
register_exception(_APP)
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))


class _StubSession:
    """只承载被测路径用到的会话动作。"""

    def __init__(self) -> None:
        self.flushed = False

    async def rollback(self) -> None:
        return None

    async def flush(self) -> None:
        self.flushed = True


async def _stub_db() -> Any:  # noqa: RUF029
    yield _StubSession()


_APP.dependency_overrides[get_db] = _stub_db
_APP.dependency_overrides[get_db_transaction] = _stub_db
# 鉴权与 RBAC 不是本用例的被测面，逐一放行以便直达业务守卫。
_APP.dependency_overrides[DependsJwtAuth.dependency] = lambda: None
_APP.dependency_overrides[rbac_verify] = lambda: None


class _FakeCatalogRow:
    def __init__(self, app_id: str = 'imagelab', config_json: dict | None = None) -> None:
        self.app_id = app_id
        self.config_json = config_json if config_json is not None else {}


def _patch_catalog(monkeypatch: pytest.MonkeyPatch, row: _FakeCatalogRow | None) -> None:
    from backend.app.hasn.crud import crud_hasn_app_catalog

    async def fake_get(db: object, pk: int) -> _FakeCatalogRow | None:  # noqa: RUF029
        return row

    monkeypatch.setattr(crud_hasn_app_catalog.hasn_app_catalog_dao, 'get', fake_get)


def _onnx_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('u2netp.onnx', b'onnx-bytes')
    return buffer.getvalue()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://test')


async def test_stage_endpoint_returns_the_unified_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功路径必须是 `{code,msg,data}` 信封——裸返回会让 daemon 解析炸。"""
    _patch_catalog(monkeypatch, _FakeCatalogRow())

    class _Ref:
        stable_url = 'https://cdn.example.com/runtime-model/imagelab/u2netp/2024.07/x-u2netp.zip'

    class _Stat:
        size = len(_onnx_zip())

    from backend.plugin.s3.service.storage_service import StorageService

    async def fake_storage(db: object, *, category: str) -> object:  # noqa: RUF029
        return object()

    async def fake_upload(storage: object, file: object, **kwargs: Any) -> _Ref:  # noqa: RUF029
        return _Ref()

    async def fake_stat(storage: object, *, object_key: str) -> _Stat:  # noqa: RUF029
        return _Stat()

    monkeypatch.setattr(StorageService, 'get_public_package_storage', fake_storage)
    monkeypatch.setattr(StorageService, 'upload_public_package_to_storage', fake_upload)
    monkeypatch.setattr(StorageService, 'stat_on_storage', fake_stat)

    async with _client() as client:
        response = await client.post(
            '/api/v1/hasn/app-catalogs/1/model-package-stage',
            files={'file': ('u2netp.zip', _onnx_zip(), 'application/zip')},
            data={'runtime_name': 'u2netp', 'version': '2024.07'},
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {'code', 'msg', 'data'}
    assert body['data']['key'].startswith('runtime-model/imagelab/u2netp/2024.07/')
    assert len(body['data']['sha256']) == 64


async def test_stage_endpoint_rejects_wrong_app_through_the_http_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """归属闸必须在 HTTP 面生效，且不得因异常处理器缺失变成 500。"""
    _patch_catalog(monkeypatch, _FakeCatalogRow(app_id='film'))
    async with _client() as client:
        response = await client.post(
            '/api/v1/hasn/app-catalogs/1/model-package-stage',
            files={'file': ('u2netp.zip', _onnx_zip(), 'application/zip')},
            data={'runtime_name': 'u2netp', 'version': '2024.07'},
        )
    assert response.status_code != 500
    assert 'imagelab' in response.text


async def test_publish_endpoint_returns_the_unified_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_catalog(monkeypatch, _FakeCatalogRow())

    async def fake_bump(scope: str, db: object) -> None:  # noqa: RUF029
        return None

    from backend.app.hasn.service import sync_invalidate_service

    monkeypatch.setattr(sync_invalidate_service, 'bump', fake_bump)

    document = {
        'payload': {
            'schema_version': 1,
            'catalog_id': 'imagelab-models',
            'release_sequence': 2026073101,
            'channel': 'stable',
            'issued_at': '2026-07-31T00:00:00Z',
            'expires_at': '2027-07-31T00:00:00Z',
            'minimum_daemon_version': '0.1.0',
            'key_id': 'hasn-release-2026',
            'models': {
                'model.rembg.u2netp': {
                    'runtime_name': 'u2netp',
                    'artifact_id': 'app.model.imagelab.u2netp',
                    'display_name': 'U²-Net 轻量抠图',
                    'purposes': ['remove_background'],
                    'license': 'Apache-2.0',
                    'version': '2024.07',
                    'filename': 'u2netp.onnx',
                    'size': 4574861,
                    'sha256': 'b' * 64,
                    'revoked': False,
                    'package': {
                        'key': 'runtime-model/imagelab/u2netp/2024.07/0123456789abcdef-u2netp.zip',
                        'url': 'https://cdn.example.com/runtime-model/imagelab/u2netp/pkg.zip',
                        'sha256': 'a' * 64,
                        'compressed_size': 4200000,
                        'installed_size': 4574861,
                    },
                }
            },
        },
        'signature': 'c' * 128,
    }

    import json as _json

    async with _client() as client:
        response = await client.post(
            '/api/v1/hasn/app-catalogs/1/model-catalog',
            files={'catalog': ('catalog.json', _json.dumps(document).encode(), 'application/json')},
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {'code', 'msg', 'data'}
    assert body['data']['signed_catalog']['payload']['catalog_id'] == 'imagelab-models'


async def test_publish_endpoint_bounds_the_upload_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过 8 MiB 的上传必须被拒，且不得先整体读进内存。"""
    _patch_catalog(monkeypatch, _FakeCatalogRow())
    oversized = b'{' + b'x' * (app_catalog_service.MAX_SIGNED_MODEL_CATALOG_BYTES + 1024)
    async with _client() as client:
        response = await client.post(
            '/api/v1/hasn/app-catalogs/1/model-catalog',
            files={'catalog': ('catalog.json', oversized, 'application/json')},
        )
    assert response.status_code != 500
    assert '8 MiB' in response.text
