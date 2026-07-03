import shutil
import zipfile

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from backend.common.dataclasses import UploadUrl
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.owner_key_auth import DependsOwnerKeyAuth
from backend.core.conf import settings

router = APIRouter()


@router.post(
    '/deploy',
    summary='网站解压部署',
    description='使用 OwnerKey 认证，上传生成的网站 zip 压缩包，解压到部署目录',
)
async def user_deploy_website(
    file: Annotated[UploadFile, File(...)],
    site_name: Annotated[str, Form(...)] = 'default',
    user_id: int = DependsOwnerKeyAuth,
) -> ResponseSchemaModel[UploadUrl]:
    if not file.filename or not file.filename.endswith('.zip'):
        raise errors.BusinessError(msg='只支持上传 .zip 格式的压缩包')

    deploy_base_dir = settings.WEBSITE_DEPLOY_DIR
    if not deploy_base_dir:
        raise errors.BusinessError(msg='服务器未配置网站部署目录 (WEBSITE_DEPLOY_DIR)')

    # 构建目标解压目录: deploy_base_dir/user_id/site_name
    # 为了安全，防止 site_name 包含 ../
    safe_site_name = Path(site_name).name
    target_dir = Path(deploy_base_dir) / str(user_id) / safe_site_name

    # 确保目录存在
    target_dir.mkdir(parents=True, exist_ok=True)

    # 临时保存上传的 zip
    temp_zip_path = target_dir / "temp_upload_deployment.zip"
    try:
        with open(temp_zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 解压 zip
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            zip_ref.extractall(target_dir)

    except zipfile.BadZipFile:
        raise errors.BusinessError(msg='解析 ZIP 文件失败，文件可能已损坏')
    finally:
        # 清理临时 zip 文件
        if temp_zip_path.exists():
            temp_zip_path.unlink()

    base_url = settings.WEBSITE_BASE_URL.rstrip('/') if settings.WEBSITE_BASE_URL else 'http://localhost'
    url = f'{base_url}/{user_id}/{safe_site_name}/index.html'

    return response_base.success(data={'url': url})
