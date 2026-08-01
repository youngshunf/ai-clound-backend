from __future__ import annotations

import hashlib
import operator
import os
import re
import stat
import zipfile

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

import yaml

from backend.common.exception import errors

MAX_PACKAGE_SIZE = 50 * 1024 * 1024
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_SIZE = 100 * 1024 * 1024
MAX_FILE_COUNT = 1000
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
    markdown: str
    files: list[dict[str, Any]]
    content_hash: str


@dataclass
class TemplatePackage:
    metadata: dict[str, Any]
    icon: PackageAsset | None
    soul_md: str
    user_md: str | None
    memory_md: str | None
    files: list[dict[str, Any]]
    content_hash: str


@dataclass
class SkillPackPackage:
    """通过边界校验的官方技能包制品。"""

    metadata: dict[str, Any]
    icon: PackageAsset | None
    hermes_yaml: str
    files: list[dict[str, Any]]
    content_hash: str


@dataclass
class WorkflowPackage:
    """通过边界校验的官方场景工作流制品。"""

    metadata: dict[str, Any]
    icon: PackageAsset | None
    files: list[dict[str, Any]]
    content_hash: str


def _validate_zip_entry(info: zipfile.ZipInfo) -> None:
    filename = info.filename
    if not filename or filename.endswith('/'):
        return
    if '\\' in filename:
        raise errors.RequestError(msg='ZIP 包含不安全路径分隔符')
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
    unix_mode = info.external_attr >> 16
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise errors.RequestError(msg='ZIP 包含不允许上传的符号链接')


def _open_validated_zip(content: bytes) -> zipfile.ZipFile:
    if len(content) > MAX_PACKAGE_SIZE:
        raise errors.RequestError(msg='ZIP 包超过大小限制')
    try:
        zf = zipfile.ZipFile(BytesIO(content), 'r')
    except zipfile.BadZipFile:
        raise errors.RequestError(msg='无效的 ZIP 文件')
    entries = zf.infolist()
    if len(entries) > MAX_FILE_COUNT:
        zf.close()
        raise errors.RequestError(msg='ZIP 文件数量超过限制')
    total_size = sum(info.file_size for info in entries if not info.is_dir())
    if total_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
        zf.close()
        raise errors.RequestError(msg='ZIP 解压后总大小超过限制')
    seen_paths: set[str] = set()
    for info in entries:
        _validate_zip_entry(info)
        if info.is_dir():
            continue
        normalized_path = PurePosixPath(info.filename).as_posix()
        if normalized_path in seen_paths:
            zf.close()
            raise errors.RequestError(msg=f'ZIP 包含重复文件路径: {normalized_path}')
        seen_paths.add(normalized_path)
    return zf


def _read_required_text(zf: zipfile.ZipFile, name: str) -> str:
    if name not in zf.namelist():
        raise errors.RequestError(msg=f'上传包缺少 {name}')
    try:
        return zf.read(name).decode('utf-8')
    except UnicodeDecodeError as exc:
        raise errors.RequestError(msg=f'{name} 必须使用 UTF-8 编码') from exc


def _read_optional_text(zf: zipfile.ZipFile, name: str) -> str | None:
    if name not in zf.namelist():
        return None
    try:
        return zf.read(name).decode('utf-8')
    except UnicodeDecodeError as exc:
        raise errors.RequestError(msg=f'{name} 必须使用 UTF-8 编码') from exc


def _load_yaml(text: str, *, name: str) -> Any:
    """把制品内 YAML 解析错误稳定映射为 4xx 请求错误。"""
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise errors.RequestError(msg=f'{name} YAML 格式错误') from exc


def _extract_frontmatter(markdown: str) -> dict[str, Any]:
    match = re.match(r'\A---\s*\n(.*?)\n---\s*(?:\n|\Z)', markdown, re.DOTALL)
    if not match:
        raise errors.RequestError(msg='SKILL.md 缺少 YAML frontmatter')
    data = _load_yaml(match.group(1), name='SKILL.md frontmatter')
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


def _package_file_manifest(zf: zipfile.ZipFile) -> tuple[list[dict[str, Any]], str]:
    """生成排序后的文件清单与内容指纹。

    指纹算法与 daemon 个人技能打包一致：对排序后的
    ``path + NUL + sha256(file) + NUL`` 连续求 SHA256。它不受 ZIP 条目顺序、
    压缩参数和时间戳影响。
    """
    entries: list[tuple[str, bytes]] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename).as_posix()
        entries.append((path, zf.read(info)))
    entries.sort(key=operator.itemgetter(0))

    top_digest = hashlib.sha256()
    files: list[dict[str, Any]] = []
    for path, content in entries:
        file_hash = hashlib.sha256(content).hexdigest()
        top_digest.update(path.encode())
        top_digest.update(b'\0')
        top_digest.update(file_hash.encode())
        top_digest.update(b'\0')
        files.append(
            {
                'path': path,
                'size': len(content),
                'sha256': file_hash,
            }
        )
    return files, top_digest.hexdigest()


def parse_skill_package(content: bytes) -> SkillPackage:
    with _open_validated_zip(content) as zf:
        markdown = _read_required_text(zf, 'SKILL.md')
        metadata = _extract_frontmatter(markdown)
        for field in ('name', 'description'):
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                raise errors.RequestError(msg=f'SKILL.md frontmatter 缺少 {field}')
        metadata['version'] = str(metadata.get('version') or '1.0.0')
        metadata['tags'] = normalize_tags(metadata.get('tags'))
        files, content_hash = _package_file_manifest(zf)
        return SkillPackage(
            metadata=metadata,
            icon=find_icon(zf),
            markdown=markdown,
            files=files,
            content_hash=content_hash,
        )


def parse_template_package(
    content: bytes,
    *,
    require_runtime_files: bool = False,
) -> TemplatePackage:
    with _open_validated_zip(content) as zf:
        template_yaml = _load_yaml(
            _read_required_text(zf, 'template.yaml'),
            name='template.yaml',
        )
        if not isinstance(template_yaml, dict):
            raise errors.RequestError(msg='template.yaml 格式错误')
        soul_md = _read_required_text(zf, 'SOUL.md')
        user_md: str | None
        memory_md: str | None
        if require_runtime_files:
            user_md = _read_required_text(zf, 'USER.md')
            memory_md = _read_required_text(zf, 'MEMORY.md')
        else:
            user_md = _read_optional_text(zf, 'USER.md')
            memory_md = _read_optional_text(zf, 'MEMORY.md')
        # AGENTS.md 已退役（2026-07-12）：它本是「工作目录/项目规范」文件被误当 persona 用，
        # runtime 从不消费它，模板也不再随包分发；故不再强制模板包携带 AGENTS.md（人格收进 SOUL.md）。
        if not template_yaml.get('name') and not template_yaml.get('display_name'):
            raise errors.RequestError(msg='template.yaml 缺少 name')
        if not template_yaml.get('description'):
            raise errors.RequestError(msg='template.yaml 缺少 description')
        template_yaml['version'] = str(template_yaml.get('version') or '1.0.0')
        template_yaml['tags'] = normalize_tags(template_yaml.get('tags'))
        files, content_hash = _package_file_manifest(zf)
        return TemplatePackage(
            metadata=template_yaml,
            icon=find_icon(zf),
            soul_md=soul_md,
            user_md=user_md,
            memory_md=memory_md,
            files=files,
            content_hash=content_hash,
        )


def parse_skill_pack_package(content: bytes) -> SkillPackPackage:
    """解析官方技能包制品，保留规范化前的完整 Hermes YAML。"""
    with _open_validated_zip(content) as zf:
        hermes_yaml = _read_required_text(zf, 'bundle.yaml')
        metadata = _load_yaml(hermes_yaml, name='bundle.yaml')
        if not isinstance(metadata, dict):
            raise errors.RequestError(msg='bundle.yaml 格式错误')
        for field in ('name', 'description'):
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                raise errors.RequestError(msg=f'bundle.yaml 缺少 {field}')
        skills = metadata.get('skills')
        if not isinstance(skills, list) or not skills:
            raise errors.RequestError(msg='bundle.yaml skills 必须为非空数组')
        if not all(isinstance(item, str) and item.strip() for item in skills):
            raise errors.RequestError(msg='bundle.yaml skills 成员必须为非空字符串')
        metadata['version'] = str(metadata.get('version') or '1.0.0')
        metadata['tags'] = normalize_tags(metadata.get('tags'))
        files, content_hash = _package_file_manifest(zf)
        return SkillPackPackage(
            metadata=metadata,
            icon=find_icon(zf),
            hermes_yaml=hermes_yaml,
            files=files,
            content_hash=content_hash,
        )


def parse_workflow_package(content: bytes) -> WorkflowPackage:
    """解析场景工作流制品，工作流图的领域校验交给权威 workflow service。"""
    with _open_validated_zip(content) as zf:
        workflow_yaml = _read_required_text(zf, 'workflow-template.yaml')
        metadata = _load_yaml(workflow_yaml, name='workflow-template.yaml')
        if not isinstance(metadata, dict):
            raise errors.RequestError(msg='workflow-template.yaml 格式错误')
        for field in ('template_key', 'name'):
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                raise errors.RequestError(msg=f'workflow-template.yaml 缺少 {field}')
        graph_spec = metadata.get('graph_spec')
        if not isinstance(graph_spec, dict):
            raise errors.RequestError(msg='workflow-template.yaml graph_spec 必须为对象')
        if not isinstance(graph_spec.get('nodes'), list):
            raise errors.RequestError(msg='workflow-template.yaml graph_spec.nodes 必须为数组')
        files, content_hash = _package_file_manifest(zf)
        return WorkflowPackage(
            metadata=metadata,
            icon=find_icon(zf),
            files=files,
            content_hash=content_hash,
        )
