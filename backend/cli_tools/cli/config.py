"""CLI 配置管理

支持从配置文件读取远程发布参数。
配置文件位置（按优先级）：
1. 当前目录 .fba.yaml
2. 用户目录 ~/.fba/config.yaml
"""
import os

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class RemoteConfig:
    """远程发布配置"""
    api_url: str | None = None
    api_key: str | None = None


@dataclass
class CliConfig:
    """CLI 配置"""
    remote: RemoteConfig | None = None

    @classmethod
    def load(cls) -> 'CliConfig':
        """加载配置文件"""
        config_paths = [
            Path.cwd() / '.fba.yaml',
            Path.cwd() / '.fba.yml',
            Path.home() / '.fba' / 'config.yaml',
            Path.home() / '.fba' / 'config.yml',
        ]

        for config_path in config_paths:
            if config_path.exists():
                return cls._load_from_file(config_path)

        return cls()

    @classmethod
    def _load_from_file(cls, path: Path) -> 'CliConfig':
        """从文件加载配置"""
        try:
            with open(path, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}

            remote_data = data.get('remote', {})
            remote_config = RemoteConfig(
                api_url=remote_data.get('api_url'),
                api_key=remote_data.get('api_key'),
            ) if remote_data else None

            return cls(remote=remote_config)
        except Exception:
            return cls()

    def get_remote_url(self) -> str | None:
        """获取远程 API URL"""
        # 优先使用环境变量
        env_url = os.environ.get('FBA_API_URL')
        if env_url:
            return env_url
        return self.remote.api_url if self.remote else None

    def get_remote_key(self) -> str | None:
        """获取远程 API Key"""
        # 优先使用环境变量
        env_key = os.environ.get('FBA_API_KEY')
        if env_key:
            return env_key
        return self.remote.api_key if self.remote else None


# 全局配置实例
_config: CliConfig | None = None


def get_config() -> CliConfig:
    """获取配置（懒加载）"""
    global _config
    if _config is None:
        _config = CliConfig.load()
    return _config
