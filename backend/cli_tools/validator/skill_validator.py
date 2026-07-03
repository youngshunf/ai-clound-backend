"""技能包验证器

验证技能包的目录结构和文件格式。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from backend.cli_tools.cli.common import (
    VersionInfo,
    format_size,
    print_error,
    print_success,
    print_warning,
    validate_skill_id,
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
class SkillConfig:
    """技能配置信息"""
    id: str
    name: str
    version: str
    description: str
    category: str | None = None
    tags: str | None = None
    pricing: str = 'free'
    author_name: str | None = None
    author_email: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SkillConfig':
        author = data.get('author', {})
        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            version=data.get('version', ''),
            description=data.get('description', ''),
            category=data.get('category'),
            tags=data.get('tags'),
            pricing=data.get('pricing', 'free'),
            author_name=author.get('name') if isinstance(author, dict) else None,
            author_email=author.get('email') if isinstance(author, dict) else None,
        )


class SkillValidator:
    """技能包验证器"""

    # 必需文件
    REQUIRED_FILES = ['config.yaml', 'SKILL.md', 'icon.svg']

    # 可选目录
    OPTIONAL_DIRS = ['scripts', 'templates', 'examples', 'prompts']

    # 排除的文件和目录
    EXCLUDED_PATTERNS = [
        '.git', '.gitignore', '.DS_Store', '__pycache__',
        '*.pyc', '.env', '.venv', 'node_modules',
    ]

    # 图标大小限制 (100KB)
    MAX_ICON_SIZE = 100 * 1024

    def __init__(self, skill_path: Path) -> None:
        self.skill_path = Path(skill_path).resolve()
        self.result = ValidationResult()
        self.config: SkillConfig | None = None

    def validate(self) -> ValidationResult:
        """执行完整验证"""
        # 1. 验证目录存在
        if not self._validate_directory():
            return self.result

        # 2. 验证必需文件
        self._validate_required_files()

        # 3. 验证 config.yaml
        if (self.skill_path / 'config.yaml').exists():
            self._validate_config_yaml()

        # 4. 验证 SKILL.md
        if (self.skill_path / 'SKILL.md').exists():
            self._validate_skill_md()

        # 5. 验证 icon.svg
        if (self.skill_path / 'icon.svg').exists():
            self._validate_icon_svg()

        return self.result

    def _validate_directory(self) -> bool:
        """验证目录存在"""
        if not self.skill_path.exists():
            self.result.add_error(f'目录不存在: {self.skill_path}')
            return False

        if not self.skill_path.is_dir():
            self.result.add_error(f'路径不是目录: {self.skill_path}')
            return False

        return True

    def _validate_required_files(self) -> None:
        """验证必需文件"""
        for filename in self.REQUIRED_FILES:
            filepath = self.skill_path / filename
            if not filepath.exists():
                self.result.add_error(f'缺少必需文件: {filename}')
            elif not filepath.is_file():
                self.result.add_error(f'{filename} 不是文件')

    def _validate_config_yaml(self) -> None:
        """验证 config.yaml"""
        config_path = self.skill_path / 'config.yaml'

        try:
            with open(config_path, encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            self.result.add_error(f'config.yaml 格式错误: {e}')
            return
        except Exception as e:
            self.result.add_error(f'无法读取 config.yaml: {e}')
            return

        if not isinstance(data, dict):
            self.result.add_error('config.yaml 应该是一个字典')
            return

        # 验证必填字段
        required_fields = ['id', 'name', 'version', 'description']
        for field in required_fields:
            if field not in data or not data[field]:
                self.result.add_error(f'config.yaml 缺少必填字段: {field}')

        # 验证 author
        author = data.get('author', {})
        if not isinstance(author, dict) or not author.get('name'):
            self.result.add_error('config.yaml 缺少 author.name')

        # 验证 id 格式
        skill_id = data.get('id', '')
        if skill_id and not validate_skill_id(skill_id):
            self.result.add_error(f'技能 ID 格式无效: {skill_id}（应为小写字母、数字、连字符）')

        # 验证版本号格式
        version = data.get('version', '')
        if version:
            try:
                VersionInfo.parse(version)
            except ValueError as e:
                self.result.add_error(str(e))

        # 验证 pricing
        pricing = data.get('pricing', 'free')
        if pricing not in ['free', 'paid', 'subscription']:
            self.result.add_warning(f'未知的 pricing 类型: {pricing}')

        # 保存配置
        if self.result.valid:
            self.config = SkillConfig.from_dict(data)

    def _validate_skill_md(self) -> None:
        """验证 SKILL.md"""
        skill_md_path = self.skill_path / 'SKILL.md'

        try:
            content = skill_md_path.read_text(encoding='utf-8')
        except Exception as e:
            self.result.add_error(f'无法读取 SKILL.md: {e}')
            return

        # 检查 YAML frontmatter
        if not content.startswith('---'):
            self.result.add_warning('SKILL.md 没有 YAML frontmatter')
            return

        # 尝试解析 frontmatter
        try:
            # 查找第二个 ---
            end_marker = content.find('---', 3)
            if end_marker == -1:
                self.result.add_error('SKILL.md frontmatter 格式错误：缺少结束标记')
                return

            frontmatter = content[3:end_marker].strip()
            metadata = yaml.safe_load(frontmatter)

            if not isinstance(metadata, dict):
                self.result.add_error('SKILL.md frontmatter 应该是一个字典')
                return

            # 验证必填字段
            if not metadata.get('name'):
                self.result.add_warning('SKILL.md frontmatter 缺少 name 字段')
            if not metadata.get('description'):
                self.result.add_warning('SKILL.md frontmatter 缺少 description 字段')

        except yaml.YAMLError as e:
            self.result.add_error(f'SKILL.md frontmatter 解析错误: {e}')

    def _validate_icon_svg(self) -> None:
        """验证 icon.svg"""
        icon_path = self.skill_path / 'icon.svg'

        try:
            stat = icon_path.stat()
            file_size = stat.st_size
        except Exception as e:
            self.result.add_error(f'无法获取 icon.svg 信息: {e}')
            return

        # 检查文件大小
        if file_size > self.MAX_ICON_SIZE:
            self.result.add_error(
                f'icon.svg 太大: {format_size(file_size)}（限制: {format_size(self.MAX_ICON_SIZE)}）'
            )

        # 检查基本 SVG 格式
        try:
            content = icon_path.read_text(encoding='utf-8')
            if '<svg' not in content.lower():
                self.result.add_error('icon.svg 不是有效的 SVG 文件')
        except Exception as e:
            self.result.add_error(f'无法读取 icon.svg: {e}')

    def print_result(self) -> None:
        """打印验证结果"""
        for error in self.result.errors:
            print_error(error)

        for warning in self.result.warnings:
            print_warning(warning)

        if self.result.valid:
            print_success('技能包验证通过')
            if self.config:
                print_success(f'技能: {self.config.name} (v{self.config.version})')
        else:
            print_error('技能包验证失败')
