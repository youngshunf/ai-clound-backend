"""网站部署接口的文件系统安全回归测试。"""

from __future__ import annotations

import zipfile

from pathlib import Path

import pytest

from fastapi import UploadFile

from backend.app.huanxing.api.v1.agent.website import agent_deploy_website
from backend.app.huanxing.api.v1.user.website import user_deploy_website
from backend.common.dataclasses import UploadUrl
from backend.common.exception import errors
from backend.core.conf import settings

pytestmark = pytest.mark.asyncio(loop_scope='module')


def _write_zip(path: Path, entries: dict[str, str]) -> None:
    """写入供部署接口读取的真实 ZIP 文件。"""
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


async def test_website_deploy_returns_dto_and_rejects_zip_path_traversal(tmp_path: Path) -> None:
    """部署成功返回 DTO，恶意 ZIP 不得写出站点目录。"""
    deploy_dir = tmp_path / 'deploy'
    valid_zip = tmp_path / 'valid.zip'
    unsafe_zip = tmp_path / 'unsafe.zip'
    _write_zip(valid_zip, {'index.html': '<h1>安全站点</h1>'})
    _write_zip(unsafe_zip, {'../escaped.html': '不应写出目标目录'})

    previous_deploy_dir = settings.WEBSITE_DEPLOY_DIR
    previous_base_url = settings.WEBSITE_BASE_URL
    settings.WEBSITE_DEPLOY_DIR = str(deploy_dir)
    settings.WEBSITE_BASE_URL = 'https://website.example'
    try:
        with valid_zip.open('rb') as source:
            response = await user_deploy_website(
                file=UploadFile(source, filename='valid.zip'),
                site_name='quality-site',
                user_id=42,
            )

        assert isinstance(response.data, UploadUrl)
        assert response.data.url == 'https://website.example/42/quality-site/index.html'
        assert (deploy_dir / '42' / 'quality-site' / 'index.html').read_text(encoding='utf-8') == '<h1>安全站点</h1>'

        with unsafe_zip.open('rb') as source, pytest.raises(errors.RequestError):
            await agent_deploy_website(
                user_id='agent-quality',
                file=UploadFile(source, filename='unsafe.zip'),
                site_name='unsafe-site',
            )

        assert not (deploy_dir / 'agent-quality' / 'escaped.html').exists()
    finally:
        settings.WEBSITE_DEPLOY_DIR = previous_deploy_dir
        settings.WEBSITE_BASE_URL = previous_base_url
