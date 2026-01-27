"""Configuration loader for code generator."""

from pathlib import Path
from typing import Any

import rtoml

from backend.core.path_conf import BASE_PATH

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / 'config.toml'


class CodeGenConfig:
    """Code generator configuration."""

    # 暴露配置文件路径
    CONFIG_FILE = CONFIG_FILE

    def __init__(self):
        """Load configuration from TOML file."""
        if not CONFIG_FILE.exists():
            raise FileNotFoundError(f'Configuration file not found: {CONFIG_FILE}')

        self._config = rtoml.load(CONFIG_FILE)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """
        Get configuration value.

        :param section: Configuration section name
        :param key: Configuration key
        :param default: Default value if not found
        :return: Configuration value
        """
        return self._config.get(section, {}).get(key, default)

    @property
    def frontend_dir(self) -> Path:
        """Get frontend directory path (absolute)."""
        frontend_dir = Path(self.get('paths', 'frontend_dir', '../clound-frontend'))
        if not frontend_dir.is_absolute():
            # 相对于 clound-backend 项目根
            frontend_dir = (BASE_PATH.parent / frontend_dir).resolve()
        return frontend_dir

    @property
    def backend_app_dir(self) -> Path:
        """Get backend app directory path (absolute)."""
        app_dir = Path(self.get('paths', 'backend_app_dir', 'app'))
        if not app_dir.is_absolute():
            # 相对于 backend/ 根目录
            app_dir = (BASE_PATH / app_dir).resolve()
        return app_dir

    @property
    def menu_sql_dir(self) -> Path:
        """Get menu SQL output directory (absolute)."""
        sql_dir = Path(self.get('paths', 'menu_sql_dir', 'backend/sql/generated'))
        if not sql_dir.is_absolute():
            sql_dir = (BASE_PATH.parent / sql_dir).resolve()
        return sql_dir

    @property
    def dict_sql_dir(self) -> Path:
        """Get dict SQL output directory (absolute)."""
        sql_dir = Path(self.get('paths', 'dict_sql_dir', 'backend/sql/generated'))
        if not sql_dir.is_absolute():
            sql_dir = (BASE_PATH.parent / sql_dir).resolve()
        return sql_dir

    @property
    def auto_execute_menu_sql(self) -> bool:
        """Whether to auto-execute menu SQL."""
        return self.get('generation', 'auto_execute_menu_sql', False)

    @property
    def auto_execute_dict_sql(self) -> bool:
        """Whether to auto-execute dict SQL."""
        return self.get('generation', 'auto_execute_dict_sql', False)

    @property
    def existing_file_behavior(self) -> str:
        """Get behavior when file exists: 'skip', 'overwrite', 'backup'."""
        return self.get('generation', 'existing_file_behavior', 'skip')

    @property
    def generate_backend(self) -> bool:
        """Whether to generate backend code."""
        return self.get('generation', 'generate_backend', True)

    @property
    def generate_frontend(self) -> bool:
        """Whether to generate frontend code."""
        return self.get('generation', 'generate_frontend', True)

    @property
    def generate_menu_sql(self) -> bool:
        """Whether to generate menu SQL."""
        return self.get('generation', 'generate_menu_sql', True)

    @property
    def generate_dict_sql(self) -> bool:
        """Whether to generate dict SQL."""
        return self.get('generation', 'generate_dict_sql', True)

    @property
    def default_db_schema(self) -> str:
        """Get default database schema."""
        return self.get('backend', 'default_db_schema', 'fba')

    @property
    def api_version(self) -> str:
        """Get API version."""
        return self.get('backend', 'api_version', 'v1')

    @property
    def default_icon(self) -> str:
        """Get default menu icon."""
        return self.get('frontend', 'default_icon', 'lucide:list')

    @property
    def parent_menu_id(self) -> int | None:
        """Get parent menu ID. Returns None if 0 or not set."""
        value = self.get('menu', 'parent_menu_id', 0)
        return value if value else None

    @property
    def menu_sort_start(self) -> int:
        """Get menu sort start value."""
        return self.get('menu', 'menu_sort_start', 100)

    @property
    def auto_dict_patterns(self) -> list[str]:
        """Get auto dict field name patterns."""
        return self.get('dict', 'auto_dict_patterns', [])

    @property
    def default_status_options(self) -> list[dict]:
        """Get default status dict options."""
        return self.get('dict', 'default_status_options', [])

    @property
    def default_type_options(self) -> list[dict]:
        """Get default type dict options."""
        return self.get('dict', 'default_type_options', [])


# Singleton instance
codegen_config = CodeGenConfig()
