from io import BytesIO

import pytest

from fastapi import UploadFile

from backend.common.exception.errors import RequestError
from backend.utils.file_ops import build_filename, upload_file_verify


def test_build_filename_rejects_missing_filename() -> None:
    """上传文件缺少名称时应返回明确的请求错误。"""
    file = UploadFile(BytesIO(b'content'), filename=None, size=7)

    with pytest.raises(RequestError, match='文件名'):
        build_filename(file)


def test_upload_file_verify_rejects_unknown_size() -> None:
    """无法确认文件大小时不能绕过上传大小限制。"""
    file = UploadFile(BytesIO(b'content'), filename='photo.jpg', size=None)

    with pytest.raises(RequestError, match='大小'):
        upload_file_verify(file)
