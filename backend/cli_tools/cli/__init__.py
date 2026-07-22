"""CLI 命令模块。"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.cli_tools.cli.app import App
    from backend.cli_tools.cli.category import Category
    from backend.cli_tools.cli.skill import Skill

__all__ = ['App', 'Category', 'Skill']


def __getattr__(name: str) -> Any:
    """按需加载命令，避免共享工具模块触发命令间的循环导入。"""
    if name == 'App':
        from backend.cli_tools.cli.app import App

        return App
    if name == 'Category':
        from backend.cli_tools.cli.category import Category

        return Category
    if name == 'Skill':
        from backend.cli_tools.cli.skill import Skill

        return Skill
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
