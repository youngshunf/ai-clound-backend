"""Template package service for marketplace hub templates."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile

from pathlib import Path
from typing import Any

from backend.app.marketplace.service.resource_id import encode_namespace, parse_resource_id
from backend.common.log import log
from backend.core.conf import settings


class AppPackageService:
    """Build ZIP packages for hasn-hub templates.

    The class name is kept for existing imports, but all public methods use the
    current template terminology.
    """

    def __init__(self) -> None:
        self.cache_dir = Path(getattr(settings, 'MARKETPLACE_CACHE_DIR', '/tmp/marketplace-cache'))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hub_local_path = Path(getattr(settings, 'HUANXING_HUB_LOCAL_PATH', '/tmp/hasn-hub'))

    async def build_template_package(self, template_id: str, version: str) -> dict[str, Any]:  # noqa: C901
        """Build a template ZIP package from hasn-hub/templates/{category}/{slug}."""
        cached = await self.get_cached_package(template_id, version)
        if cached:
            log.info(f"Using cached template package for {template_id} v{version}")
            return cached

        namespace, slug = parse_resource_id(template_id)
        category = namespace.removeprefix('huanxing/')
        template_dir = self.hub_local_path / 'templates' / category / slug
        if not template_dir.exists():
            raise FileNotFoundError(f"Template directory not found: {template_dir}")

        templates_root = self.hub_local_path / 'templates'
        base_dir = templates_root / '_base'
        base_desktop_dir = templates_root / '_base_desktop'
        tmpdir = Path(tempfile.mkdtemp(prefix=f"huanxing-template-{slug}-"))

        try:
            if base_dir.exists():
                shutil.copytree(base_dir, tmpdir / '_base')
            if base_desktop_dir.exists():
                shutil.copytree(base_desktop_dir, tmpdir / '_base_desktop')

            for item in template_dir.iterdir():
                if item.name.startswith('.'):
                    continue
                if item.name in {'icon.png', 'icon.svg', 'icon.jpg', 'icon.jpeg'}:
                    continue
                dst = tmpdir / item.name
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)

            package_dir = self.cache_dir / 'templates' / encode_namespace(namespace) / slug
            package_dir.mkdir(parents=True, exist_ok=True)
            zip_path = package_dir / f'{version}.zip'

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(tmpdir):
                    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'node_modules')]
                    for file in files:
                        if file.startswith('.'):
                            continue
                        file_path = Path(root) / file
                        zf.write(file_path, file_path.relative_to(tmpdir))

            return {
                'package_path': str(zip_path),
                'file_hash': await self._calculate_hash(zip_path),
                'file_size': zip_path.stat().st_size,
            }
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def get_cached_package(self, template_id: str, version: str) -> dict[str, Any] | None:
        namespace, slug = parse_resource_id(template_id)
        zip_path = self.cache_dir / 'templates' / encode_namespace(namespace) / slug / f'{version}.zip'
        if not zip_path.exists():
            return None
        return {
            'package_path': str(zip_path),
            'file_hash': await self._calculate_hash(zip_path),
            'file_size': zip_path.stat().st_size,
        }

    async def invalidate_cache(self, template_id: str, version: str | None = None) -> None:
        namespace, slug = parse_resource_id(template_id)
        template_cache_dir = self.cache_dir / 'templates' / encode_namespace(namespace) / slug
        if version:
            zip_path = template_cache_dir / f'{version}.zip'
            if zip_path.exists():
                zip_path.unlink()
            return
        if template_cache_dir.exists():
            shutil.rmtree(template_cache_dir)

    async def cleanup_old_packages(self, keep_versions: int = 3) -> None:
        templates_cache_dir = self.cache_dir / 'templates'
        if not templates_cache_dir.exists():
            return
        for template_dir in templates_cache_dir.glob('*/*'):
            if not template_dir.is_dir():
                continue
            version_files = sorted(
                template_dir.glob('*.zip'),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old_file in version_files[keep_versions:]:
                old_file.unlink()

    async def _calculate_hash(self, file_path: Path) -> str:
        sha256 = hashlib.sha256()
        with file_path.open('rb') as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()


app_package_service = AppPackageService()
