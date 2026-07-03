"""应用包验证器

验证应用包的目录结构和 manifest.json 格式。
"""

import json
import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.cli_tools.cli.common import (
    VersionInfo,
    format_size,
    print_error,
    print_success,
    print_warning,
    validate_app_id,
)


@dataclass
class ValidationResult:
    """验证结果"""
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


@dataclass
class AppManifest:
    """应用清单信息"""
    id: str
    name: str
    version: str
    description: str
    app_type: str = 'app'
    author_name: str | None = None
    author_email: str | None = None
    license: str | None = None
    homepage: str | None = None
    pricing_type: str = 'free'
    skill_dependencies: list[str] = field(default_factory=list)
    category: str | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'AppManifest':
        author = data.get('author', {})
        pricing = data.get('pricing', {})
        marketplace = data.get('marketplace', {})
        capabilities = data.get('capabilities', {})

        # 技能依赖优先从 capabilities.skills 读取，其次从 skillDependencies
        skill_deps = []
        if isinstance(capabilities, dict) and capabilities.get('skills'):
            skill_deps = capabilities.get('skills', [])
        elif data.get('skillDependencies'):
            skill_deps = data.get('skillDependencies', [])

        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            version=data.get('version', ''),
            description=data.get('description', ''),
            app_type=data.get('type', 'app'),
            author_name=author.get('name') if isinstance(author, dict) else None,
            author_email=author.get('email') if isinstance(author, dict) else None,
            license=data.get('license'),
            homepage=data.get('homepage'),
            pricing_type=pricing.get('type', 'free') if isinstance(pricing, dict) else 'free',
            skill_dependencies=skill_deps,
            category=marketplace.get('category') if isinstance(marketplace, dict) else None,
            tags=marketplace.get('tags', []) if isinstance(marketplace, dict) else [],
        )


class AppValidator:
    """应用包验证器"""

    # 必需文件
    REQUIRED_FILES = ['manifest.json']

    # 图标路径
    ICON_PATH = 'assets/icon.svg'

    # 图标大小限制 (100KB)
    MAX_ICON_SIZE = 100 * 1024

    def __init__(self, app_path: Path) -> None:
        self.app_path = Path(app_path).resolve()
        self.result = ValidationResult()
        self.manifest: AppManifest | None = None

    def validate(self) -> ValidationResult:
        """执行完整验证"""
        # 1. 验证目录存在
        if not self._validate_directory():
            return self.result

        # 2. 验证必需文件
        self._validate_required_files()

        # 3. 验证 manifest.json
        if (self.app_path / 'manifest.json').exists():
            self._validate_manifest()

        # 4. 验证图标
        icon_path = self.app_path / self.ICON_PATH
        if icon_path.exists():
            self._validate_icon(icon_path)
        else:
            self.result.add_warning(f'缺少图标文件: {self.ICON_PATH}')

        return self.result

    def _validate_directory(self) -> bool:
        """验证目录存在"""
        if not self.app_path.exists():
            self.result.add_error(f'目录不存在: {self.app_path}')
            return False

        if not self.app_path.is_dir():
            self.result.add_error(f'路径不是目录: {self.app_path}')
            return False

        return True

    def _validate_required_files(self) -> None:
        """验证必需文件"""
        for filename in self.REQUIRED_FILES:
            filepath = self.app_path / filename
            if not filepath.exists():
                self.result.add_error(f'缺少必需文件: {filename}')
            elif not filepath.is_file():
                self.result.add_error(f'{filename} 不是文件')

    def _validate_manifest(self) -> None:
        """验证 manifest.json"""
        manifest_path = self.app_path / 'manifest.json'

        try:
            with open(manifest_path, encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            self.result.add_error(f'manifest.json 格式错误: {e}')
            return
        except Exception as e:
            self.result.add_error(f'无法读取 manifest.json: {e}')
            return

        if not isinstance(data, dict):
            self.result.add_error('manifest.json 应该是一个对象')
            return

        # 验证必填字段
        required_fields = ['id', 'name', 'version', 'description']
        for field in required_fields:
            if field not in data or not data[field]:
                self.result.add_error(f'manifest.json 缺少必填字段: {field}')

        # 验证 author
        author = data.get('author', {})
        if not isinstance(author, dict) or not author.get('name'):
            self.result.add_error('manifest.json 缺少 author.name')

        # 验证 id 格式
        app_id = data.get('id', '')
        if app_id and not validate_app_id(app_id):
            self.result.add_error(f'应用 ID 格式无效: {app_id}（应为 app.name 或 plugin.name 格式）')

        # 验证版本号格式
        version = data.get('version', '')
        if version:
            try:
                VersionInfo.parse(version)
            except ValueError as e:
                self.result.add_error(str(e))

        # 验证 pricing
        pricing = data.get('pricing', {})
        if isinstance(pricing, dict):
            pricing_type = pricing.get('type', 'free')
            if pricing_type not in ['free', 'paid', 'freemium', 'subscription']:
                self.result.add_warning(f'未知的 pricing.type: {pricing_type}')

        # 验证 skillDependencies 格式
        skill_deps = data.get('skillDependencies', [])
        if skill_deps and isinstance(skill_deps, list):
            for dep in skill_deps:
                if not self._validate_skill_dependency(dep):
                    self.result.add_warning(f'技能依赖格式可能不正确: {dep}')

        # 保存清单
        if self.result.valid:
            self.manifest = AppManifest.from_dict(data)

    def _validate_skill_dependency(self, dep: str) -> bool:
        """验证技能依赖格式 (skill-id@^version)"""
        if not isinstance(dep, str):
            return False

        # 简单格式验证: skill-id 或 skill-id@version
        pattern = r'^[a-z][a-z0-9-]*(@[\^~]?\d+\.\d+\.\d+)?$'
        return bool(re.match(pattern, dep))

    def _validate_icon(self, icon_path: Path) -> None:
        """验证图标文件"""
        try:
            stat = icon_path.stat()
            file_size = stat.st_size
        except Exception as e:
            self.result.add_error(f'无法获取图标信息: {e}')
            return

        # 检查文件大小
        if file_size > self.MAX_ICON_SIZE:
            self.result.add_error(
                f'图标文件太大: {format_size(file_size)}（限制: {format_size(self.MAX_ICON_SIZE)}）'
            )

        # 检查基本 SVG 格式
        try:
            content = icon_path.read_text(encoding='utf-8')
            if '<svg' not in content.lower():
                self.result.add_error('图标不是有效的 SVG 文件')
        except Exception as e:
            self.result.add_error(f'无法读取图标文件: {e}')

    def print_result(self) -> None:
        """打印验证结果"""
        for error in self.result.errors:
            print_error(error)

        for warning in self.result.warnings:
            print_warning(warning)

        if self.result.valid:
            print_success('应用包验证通过')
            if self.manifest:
                print_success(f'应用: {self.manifest.name} (v{self.manifest.version})')
        else:
            print_error('应用包验证失败')
