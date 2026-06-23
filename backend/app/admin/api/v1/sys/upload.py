import uuid

from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.utils.file_ops import build_object_url, pick_public_storage, write_bytes
from backend.utils.timezone import timezone

router = APIRouter()

# 通用图片上传白名单：jpg/png/gif/webp/svg，≤50MB。
# 说明：SVG 作为图标资产由 <img> 渲染（脚本不执行），且本接口仅限管理员（Owner JWT）上传，
# 与 marketplace icon.svg 落公共桶的既有约定一致。
ALLOWED_IMAGE_TYPES = ('image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml')
MAX_IMAGE_BYTES = 50 * 1024 * 1024  # 50MB（与 daemon/webui 图片上限一致）
IMAGE_EXT_BY_MIME = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'image/svg+xml': 'svg',
}


@router.post('/image', summary='通用图片上传', dependencies=[DependsJwtAuth])
async def upload_image(
    db: CurrentSession,
    file: Annotated[UploadFile, File(description='图片文件')],
) -> ResponseSchemaModel[dict]:
    """
    通用图片上传到 S3 对象存储，按 年/月/日 组织目录。

    - 鉴权：Owner JWT（本地 daemon 以主人身份代理；WebUI 只调 daemon，不直连云端）。
    - 支持格式：jpg / jpeg / png / gif / webp / svg。
    - 最大体积：50MB。
    - 对象 key：``images/{YYYY}/{MM}/{DD}/{uuid}.{ext}``，文件名用 uuid 防冲突、不暴露原始名。
    - 返回：稳定的 CDN / S3 URL，可直接写入文章封面、正文配图等。
    """
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise errors.RequestError(msg='不支持的图片格式，仅支持 jpg、png、gif、webp、svg')

    content = await file.read()
    if not content:
        raise errors.RequestError(msg='上传图片不能为空')
    if len(content) > MAX_IMAGE_BYTES:
        raise errors.RequestError(msg='图片大小不能超过 50MB')

    storages = await s3_storage_dao.get_all(db)
    s3_storage = pick_public_storage(storages)
    if not s3_storage:
        raise errors.NotFoundError(
            msg='S3 存储配置不存在。请先在管理后台配置 S3 存储（系统管理 -> S3存储管理），'
            '或使用兼容 S3 的本地存储服务（如 MinIO）。'
        )

    now = timezone.now()
    ext = IMAGE_EXT_BY_MIME.get(file.content_type, 'png')
    path = f'images/{now:%Y/%m/%d}/{uuid.uuid4().hex}.{ext}'

    await write_bytes(s3_storage, path, content, file.content_type)

    return response_base.success(data={'url': build_object_url(s3_storage, path)})
