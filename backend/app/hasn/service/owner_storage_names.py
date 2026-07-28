"""用户云存储逻辑名称的唯一服务端实现。"""

from __future__ import annotations

import posixpath
import re
import unicodedata

from backend.common.exception import errors
from backend.common.response.response_code import StandardResponseCode

MAX_DISPLAY_NAME_LENGTH = 255
_CONTROL_OR_SEPARATOR = re.compile(r'[\x00-\x1f\x7f/\\]')
_SPACES = re.compile(r'\s+')
_WINDOWS_RESERVED = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.IGNORECASE)


def _invalid_name(name: str) -> errors.RequestError:
    return errors.RequestError(
        code=StandardResponseCode.HTTP_422,
        msg='STORAGE_NAME_INVALID',
        data={'name': name[:MAX_DISPLAY_NAME_LENGTH]},
    )


def normalize_storage_name(name: str) -> str:
    """按 NFKC、空白折叠与 casefold 生成跨端冲突键。"""
    if _CONTROL_OR_SEPARATOR.search(name):
        raise _invalid_name(name)
    normalized = unicodedata.normalize('NFKC', name).strip().strip('.').strip()
    normalized = _SPACES.sub(' ', normalized)
    if not normalized or _WINDOWS_RESERVED.match(normalized):
        raise _invalid_name(name)
    return normalized.casefold()


def _truncate_preserving_extension(name: str, limit: int) -> str:
    stem, extension = posixpath.splitext(name)
    if not extension or len(extension) >= limit:
        return name[:limit]
    return f'{stem[: limit - len(extension)]}{extension}'


def display_name_for_upload(original_name: str) -> str:
    """生成最多 255 字符的上传展示名，并在截断时尽量保留扩展名。"""
    normalized_input = unicodedata.normalize('NFKC', original_name).strip().strip('.').strip()
    if _CONTROL_OR_SEPARATOR.search(normalized_input) or not normalized_input:
        raise _invalid_name(original_name)
    display_name = _truncate_preserving_extension(normalized_input, MAX_DISPLAY_NAME_LENGTH)
    normalize_storage_name(display_name)
    return display_name


def suffixed_name(display_name: str, sequence: int) -> str:
    """把重名序号插到扩展名前，并保持 255 字符上限。"""
    if sequence < 2:
        return display_name_for_upload(display_name)
    stem, extension = posixpath.splitext(display_name)
    suffix = f' ({sequence})'
    room = MAX_DISPLAY_NAME_LENGTH - len(extension) - len(suffix)
    if room <= 0:
        candidate = f'{display_name[: MAX_DISPLAY_NAME_LENGTH - len(suffix)]}{suffix}'
    else:
        candidate = f'{stem[:room]}{suffix}{extension}'
    normalize_storage_name(candidate)
    return candidate
