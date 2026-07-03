"""CLI 共享工具函数"""

import re

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


@dataclass
class VersionInfo:
    """版本信息"""
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f'{self.major}.{self.minor}.{self.patch}'

    @classmethod
    def parse(cls, version: str) -> 'VersionInfo':
        """解析版本号字符串"""
        match = re.match(r'^(\d+)\.(\d+)\.(\d+)$', version.strip())
        if not match:
            raise ValueError(f'无效的版本号格式: {version}，应为 x.y.z 格式')
        return cls(
            major=int(match.group(1)),
            minor=int(match.group(2)),
            patch=int(match.group(3)),
        )

    def bump(self, bump_type: Literal['patch', 'minor', 'major']) -> 'VersionInfo':
        """递增版本号"""
        if bump_type == 'patch':
            return VersionInfo(self.major, self.minor, self.patch + 1)
        if bump_type == 'minor':
            return VersionInfo(self.major, self.minor + 1, 0)
        if bump_type == 'major':
            return VersionInfo(self.major + 1, 0, 0)
        raise ValueError(f'无效的版本递增类型: {bump_type}')


def validate_skill_id(skill_id: str) -> bool:
    """验证技能 ID 格式（小写字母、数字、连字符）"""
    return bool(re.match(r'^[a-z][a-z0-9-]*[a-z0-9]$', skill_id)) or bool(re.match(r'^[a-z]$', skill_id))


def validate_app_id(app_id: str) -> bool:
    """验证应用 ID 格式（app.name 或 plugin.name）"""
    return bool(re.match(r'^(app|plugin)\.[a-z][a-z0-9-]*[a-z0-9]$', app_id))


def print_success(message: str) -> None:
    """打印成功消息"""
    console.print(f'[green]✓[/] {message}')


def print_error(message: str) -> None:
    """打印错误消息"""
    console.print(f'[red]✗[/] {message}')


def print_warning(message: str) -> None:
    """打印警告消息"""
    console.print(f'[yellow]⚠[/] {message}')


def print_info(message: str) -> None:
    """打印信息消息"""
    console.print(f'[blue]ℹ[/] {message}')


def print_header(title: str) -> None:
    """打印标题"""
    console.print(f'\n[bold cyan]{title}[/]\n')


def print_panel(title: str, content: str, border_style: str = 'cyan') -> None:
    """打印面板"""
    panel_content = Text(content)
    console.print(Panel(panel_content, title=title, border_style=border_style, padding=(1, 2)))


def format_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size / 1024 / 1024:.1f} MB'


def get_relative_path(path: Path, base: Path) -> str:
    """获取相对路径字符串"""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)
