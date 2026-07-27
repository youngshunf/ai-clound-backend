from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile

from backend.common.dataclasses import UploadUrl
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.utils.file_ops import build_object_url, write_file
from backend.utils.file_ops import upload_file_verify

router = APIRouter()


@router.post(
    '/upload',
    summary='S3 文件上传',
    dependencies=[
        Depends(RequestPermission('s3:file:upload')),
        DependsRBAC,
    ],
)
async def upload_s3_files(
    db: CurrentSession, file: Annotated[UploadFile, File()], storage: Annotated[int, Query(description='S3 存储 ID')]
) -> ResponseSchemaModel[UploadUrl]:
    s3_storage = await s3_storage_dao.get(db, storage)
    if not s3_storage:
        raise errors.NotFoundError(msg='S3 存储不存在')
    upload_file_verify(file)
    filename = file.filename
    if not filename:
        raise errors.RequestError(msg='上传文件缺少文件名')
    await write_file(s3_storage, file)

    return response_base.success(data={'url': build_object_url(s3_storage, filename)})
