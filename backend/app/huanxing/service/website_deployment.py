"""网站 ZIP 部署的路径校验与安全解压。"""

from __future__ import annotations

import zipfile

from pathlib import Path
from typing import BinaryIO

from backend.common.exception import errors


def build_deploy_target(*, deploy_base_dir: str, owner_id: str, site_name: str) -> tuple[str, Path]:
    """校验站点名并生成受部署根目录约束的目标路径。"""
    safe_site_name = Path(site_name).name
    if safe_site_name != site_name or safe_site_name in {'.', '..'}:
        raise errors.RequestError(msg='站点名称只能是单层目录名')

    return safe_site_name, Path(deploy_base_dir) / owner_id / safe_site_name


def copy_uploaded_archive(source: BinaryIO, destination: Path) -> None:
    """将上传流写入部署目录内的临时 ZIP 文件。"""
    with destination.open('wb') as target:
        while chunk := source.read(64 * 1024):
            target.write(chunk)


def extract_zip_within_target(archive_path: Path, target_dir: Path) -> None:
    """仅解压所有成员均位于目标目录内的 ZIP 文件。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()

    with zipfile.ZipFile(archive_path, 'r') as archive:
        for member in archive.infolist():
            member_path = Path(member.filename.replace('\\', '/'))
            destination = (target_dir / member_path).resolve()
            if member_path.is_absolute() or '..' in member_path.parts or not destination.is_relative_to(target_root):
                raise errors.RequestError(msg='ZIP 包含越界文件路径')
        archive.extractall(target_dir)
