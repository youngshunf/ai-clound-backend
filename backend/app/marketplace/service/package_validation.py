from __future__ import annotations

import os
import re
import zipfile

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

import yaml

from backend.common.exception import errors

MAX_PACKAGE_SIZE = 50 * 1024 * 1024
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PATH_DEPTH = 12
BLOCKED_PARTS = {
    '.git',
    '.hg',
    '.svn',
    '__macosx',
    '__pycache__',
    'node_modules',
    '.venv',
    'venv',
    'dist',
    'build',
}
BLOCKED_NAMES = {
    '.ds_store',
    '.env',
    '.env.local',
    '.env.production',
    'id_rsa',
    'id_dsa',
    'id_ed25519',
    'thumbs.db',
}
BLOCKED_SUFFIXES = ('.key', '.pem', '.p12', '.pfx')


@dataclass
class PackageAsset:
    filename: str
    content: bytes


@dataclass
class SkillPackage:
    metadata: dict[str, Any]
    icon: PackageAsset | None


@dataclass
class TemplatePackage:
    metadata: dict[str, Any]
    icon: PackageAsset | None


def _validate_zip_entry(info: zipfile.ZipInfo) -> None:
    filename = info.filename
    if not filename or filename.endswith('/'):
        return
    path = PurePosixPath(filename)
    if path.is_absolute() or '..' in path.parts:
        raise errors.RequestError(msg='ZIP 包含不安全路径')
    if len(path.parts) > MAX_PATH_DEPTH:
        raise errors.RequestError(msg='ZIP 目录层级过深')
    if any(part.startswith('.') for part in path.parts):
        raise errors.RequestError(msg='ZIP 包含不允许上传的隐藏文件')
    lowered_parts = {part.lower() for part in path.parts}
    if BLOCKED_PARTS & lowered_parts:
        raise errors.RequestError(msg='ZIP 包含不允许上传的目录')
    lowered_name = path.name.lower()
    if (
        lowered_name in BLOCKED_NAMES
        or lowered_name.startswith('.env')
        or lowered_name.endswith(BLOCKED_SUFFIXES)
    ):
        raise errors.RequestError(msg='ZIP 包含敏感文件')
    if info.file_size > MAX_FILE_SIZE:
        raise errors.RequestError(msg='ZIP 包含超出大小限制的文件')


def _open_validated_zip(content: bytes) -> zipfile.ZipFile:
    if len(content) > MAX_PACKAGE_SIZE:
        raise errors.RequestError(msg='ZIP 包超过大小限制')
    try:
        zf = zipfile.ZipFile(BytesIO(content), 'r')
    except zipfile.BadZipFile:
        raise errors.RequestError(msg='无效的 ZIP 文件')
    for info in zf.infolist():
        _validate_zip_entry(info)
    return zf


def _read_required_text(zf: zipfile.ZipFile, name: str) -> str:
    if name not in zf.namelist():
        raise errors.RequestError(msg=f'上传包缺少 {name}')
    return zf.read(name).decode('utf-8')


def _extract_frontmatter(markdown: str) -> dict[str, Any]:
    match = re.match(r'\A---\s*\n(.*?)\n---\s*(?:\n|\Z)', markdown, re.DOTALL)
    if not match:
        raise errors.RequestError(msg='SKILL.md 缺少 YAML frontmatter')
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        raise errors.RequestError(msg='SKILL.md frontmatter 格式错误')
    return data


def normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        parts = tags.split(',')
    elif isinstance(tags, list):
        parts = tags
    else:
        parts = [str(tags)]
    normalized = []
    for tag in parts:
        value = str(tag).strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def find_icon(zf: zipfile.ZipFile) -> PackageAsset | None:
    for icon_path in (
        'icon.svg',
        'icon.png',
        'icon.jpg',
        'icon.jpeg',
        'assets/icon.svg',
        'assets/icon.png',
        'assets/icon.jpg',
    ):
        if icon_path in zf.namelist():
            return PackageAsset(filename=os.path.basename(icon_path), content=zf.read(icon_path))
    return None


def parse_skill_package(content: bytes) -> SkillPackage:
    with _open_validated_zip(content) as zf:
        metadata = _extract_frontmatter(_read_required_text(zf, 'SKILL.md'))
        for field in ('name', 'description'):
            if not metadata.get(field):
                raise errors.RequestError(msg=f'SKILL.md frontmatter 缺少 {field}')
        metadata['version'] = str(metadata.get('version') or '1.0.0')
        metadata['tags'] = normalize_tags(metadata.get('tags'))
        return SkillPackage(metadata=metadata, icon=find_icon(zf))


def parse_template_package(content: bytes) -> TemplatePackage:
    with _open_validated_zip(content) as zf:
        template_yaml = yaml.safe_load(_read_required_text(zf, 'template.yaml')) or {}
        if not isinstance(template_yaml, dict):
            raise errors.RequestError(msg='template.yaml 格式错误')
        _read_required_text(zf, 'SOUL.md')
        # AGENTS.md 已退役（2026-07-12）：它本是「工作目录/项目规范」文件被误当 persona 用，
        # runtime 从不消费它，模板也不再随包分发；故不再强制模板包携带 AGENTS.md（人格收进 SOUL.md）。
        if not template_yaml.get('name') and not template_yaml.get('display_name'):
            raise errors.RequestError(msg='template.yaml 缺少 name')
        if not template_yaml.get('description'):
            raise errors.RequestError(msg='template.yaml 缺少 description')
        template_yaml['version'] = str(template_yaml.get('version') or '1.0.0')
        template_yaml['tags'] = normalize_tags(template_yaml.get('tags'))
        return TemplatePackage(metadata=template_yaml, icon=find_icon(zf))
