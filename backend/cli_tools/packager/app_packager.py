"""应用打包器

将应用目录打包为 ZIP 文件。
"""

import hashlib
import io
import zipfile

from dataclasses import dataclass
from pathlib import Path

from backend.cli_tools.cli.common import format_size, print_info


@dataclass
class PackageResult:
    """打包结果"""
    content: bytes
    file_hash: str
    file_size: int
    file_count: int


class AppPackager:
    """应用打包器"""

    # 排除的文件和目录
    EXCLUDED_PATTERNS = {
        '.git', '.gitignore', '.DS_Store', '__pycache__',
        '.pyc', '.env', '.venv', 'node_modules', '.idea',
        '.vscode', '*.egg-info', 'dist', 'build',
    }

    def __init__(self, app_path: Path) -> None:
        self.app_path = Path(app_path).resolve()

    def package(self) -> PackageResult:
        """打包应用目录为 ZIP"""
        buffer = io.BytesIO()
        file_count = 0

        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in self._iter_files():
                arcname = file_path.relative_to(self.app_path)
                zf.write(file_path, arcname)
                file_count += 1

        content = buffer.getvalue()
        file_hash = hashlib.sha256(content).hexdigest()
        file_size = len(content)

        return PackageResult(
            content=content,
            file_hash=file_hash,
            file_size=file_size,
            file_count=file_count,
        )

    def _iter_files(self):
        """迭代所有需要打包的文件"""
        for path in self.app_path.rglob('*'):
            if path.is_file() and not self._should_exclude(path):
                yield path

    def _should_exclude(self, path: Path) -> bool:
        """判断是否应该排除该文件"""
        parts = path.relative_to(self.app_path).parts
        for part in parts:
            if part in self.EXCLUDED_PATTERNS:
                return True
            for pattern in self.EXCLUDED_PATTERNS:
                if '*' in pattern:
                    import fnmatch
                    if fnmatch.fnmatch(part, pattern):
                        return True
        return False

    def preview(self) -> list[tuple[str, int]]:
        """预览将要打包的文件列表"""
        files = []
        for path in self._iter_files():
            rel_path = path.relative_to(self.app_path)
            size = path.stat().st_size
            files.append((str(rel_path), size))
        return sorted(files)

    def print_preview(self) -> None:
        """打印打包预览"""
        files = self.preview()
        total_size = sum(size for _, size in files)

        print_info(f'将要打包 {len(files)} 个文件:')
        for rel_path, size in files:
            print(f'  {rel_path} ({format_size(size)})')
        print_info(f'总大小: {format_size(total_size)}')
