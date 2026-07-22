import zipfile

from typing import Annotated

from anyio import to_thread
from fastapi import APIRouter, File, Form, UploadFile

from backend.app.huanxing.service.website_deployment import (
    build_deploy_target,
    copy_uploaded_archive,
    extract_zip_within_target,
)
from backend.common.dataclasses import UploadUrl
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.core.conf import settings

router = APIRouter()


@router.post(
    '/deploy',
    summary='Agent 专用网站部署',
    description='Agent 调用上传生成的网站 zip 压缩包，解压到部署目录',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_deploy_website(
    user_id: Annotated[str, Form(...)],
    file: Annotated[UploadFile, File(...)],
    site_name: Annotated[str, Form(...)] = 'default',
) -> ResponseSchemaModel[UploadUrl]:
    """Agent 调用进行网站 ZIP 上传与部署"""

    if not file.filename or not file.filename.endswith('.zip'):
        raise errors.RequestError(msg='只支持上传 .zip 格式的压缩包')

    deploy_base_dir = settings.WEBSITE_DEPLOY_DIR
    if not deploy_base_dir:
        raise errors.ServerError(msg='服务器未配置网站部署目录 (WEBSITE_DEPLOY_DIR)')

    safe_site_name, target_dir = build_deploy_target(
        deploy_base_dir=deploy_base_dir,
        owner_id=user_id,
        site_name=site_name,
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    # 临时保存上传的 zip
    temp_zip_path = target_dir / 'temp_upload_deployment.zip'
    try:
        await to_thread.run_sync(copy_uploaded_archive, file.file, temp_zip_path)
        await to_thread.run_sync(extract_zip_within_target, temp_zip_path, target_dir)

    except zipfile.BadZipFile:
        raise errors.RequestError(msg='解析 ZIP 文件失败，文件可能已损坏')
    finally:
        # 清理临时 zip 文件
        if temp_zip_path.exists():
            temp_zip_path.unlink()

    base_url = settings.WEBSITE_BASE_URL.rstrip('/') if settings.WEBSITE_BASE_URL else 'http://localhost'
    url = f'{base_url}/{user_id}/{safe_site_name}/index.html'

    return response_base.success(data=UploadUrl(url=url))
