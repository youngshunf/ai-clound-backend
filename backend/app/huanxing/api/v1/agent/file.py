import urllib.parse

from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from backend.common.dataclasses import UploadUrl
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.utils.file_ops import write_file
from backend.utils.timezone import timezone

router = APIRouter()


@router.post(
    '/upload',
    summary='Agent 专用文件上传',
    description='Agent 调用进行 OSS 上传，需要携带 X-Agent-Key 请求头',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_upload_s3_files(
    db: CurrentSession, user_id: str, file: Annotated[UploadFile, File()]
) -> ResponseSchemaModel[UploadUrl]:
    """Agent 调用进行 OSS 上传，需要携带 X-Agent-Key 请求头"""
    s3_storages = await s3_storage_dao.get_all(db)
    if not s3_storages:
        raise errors.NotFoundError(msg='系统未开启或未配置 S3 存储')

    # 取第一个 S3 配置作为 Agent 使用的配置
    s3_storage = s3_storages[0]

    if not file or not file.filename:
        raise errors.RequestError(msg='上传文件不能为空')

    date_str = timezone.now().strftime('%Y/%m/%d')
    original_filename = file.filename
    # 存储路径: agent_uploads/{user_id}/年/月/日/filename（年/月/日层级目录，加前缀防冲突）
    file.filename = f'agent_uploads/{user_id}/{date_str}/{original_filename}'

    await write_file(s3_storage, file)

    encoded_filename = urllib.parse.quote(file.filename)

    # 构建完整 URL，参考 plugin/s3/api/v1/file.py
    if s3_storage.cdn_domain:
        base_url = s3_storage.cdn_domain.rstrip('/')
        url = f'{base_url}/{encoded_filename}'
    else:
        bucket_path = f'/{s3_storage.bucket}'
        if s3_storage.prefix:
            prefix = s3_storage.prefix if s3_storage.prefix.startswith('/') else f'/{s3_storage.prefix}'
            url = f'{s3_storage.endpoint}{bucket_path}{prefix}/{encoded_filename}'
        else:
            url = f'{s3_storage.endpoint}{bucket_path}/{encoded_filename}'

    return response_base.success(data=UploadUrl(url=url))
