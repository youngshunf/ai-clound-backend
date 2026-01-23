"""
平台适配器集成模块

从 agent-core 导入平台适配器，供云端使用。
所有平台配置从 YAML 文件加载，确保云端和桌面端具有相同的平台能力。

@author Ysf
@date 2026-01-22
"""

from typing import Any

# 从 agent-core 导入平台适配器
from agent_core.platforms import (
    PLATFORM_ADAPTERS,
    AdaptedContent,
    BilibiliAdapter,
    ContentSpec,
    DouyinAdapter,
    LoginResult,
    # 基类和数据模型
    PlatformAdapter,
    PublishResult,
    UserProfile,
    WechatAdapter,
    # 适配器
    XiaohongshuAdapter,
    # 工具函数
    get_adapter,
    list_platforms,
)

# 兼容别名
ContentConstraints = ContentSpec


def get_supported_platforms() -> list[dict[str, Any]]:
    """
    获取支持的平台列表

    Returns:
        平台信息列表
    """
    platforms = []
    for name in list_platforms():
        adapter = get_adapter(name)
        platforms.append({
            "name": adapter.platform_name,
            "display_name": adapter.platform_display_name,
            "login_url": adapter.login_url,
            "spec": {
                "title_max_length": adapter.spec.title_max_length,
                "content_max_length": adapter.spec.content_max_length,
                "image_max_count": adapter.spec.image_max_count,
                "video_max_count": adapter.spec.video_max_count,
                "supported_formats": adapter.spec.supported_formats,
            },
        })
    return platforms


def get_platform_spec(platform: str) -> ContentSpec | None:
    """
    获取平台内容规格

    Args:
        platform: 平台名称

    Returns:
        内容规格或 None
    """
    try:
        adapter = get_adapter(platform)
        return adapter.spec
    except ValueError:
        return None


def get_platform_url(platform: str, url_name: str) -> str | None:
    """
    获取平台 URL

    Args:
        platform: 平台名称
        url_name: URL 名称 (home, login, publish, etc.)

    Returns:
        URL 或 None
    """
    try:
        adapter = get_adapter(platform)
        return adapter.get_url(url_name)
    except (ValueError, KeyError):
        return None


def get_platform_selector(platform: str, selector_name: str, default: str = "") -> str:
    """
    获取平台选择器

    Args:
        platform: 平台名称
        selector_name: 选择器名称
        default: 默认值

    Returns:
        选择器字符串
    """
    try:
        adapter = get_adapter(platform)
        return adapter.get_selector(selector_name, default)
    except (ValueError, KeyError):
        return default


def adapt_content_for_platform(
    platform: str,
    title: str,
    content: str,
    images: list[str] | None = None,
    videos: list[str] | None = None,
    hashtags: list[str] | None = None,
) -> AdaptedContent | None:
    """
    为指定平台适配内容

    Args:
        platform: 平台名称
        title: 标题
        content: 内容
        images: 图片列表
        videos: 视频列表
        hashtags: 话题标签列表

    Returns:
        适配后的内容或 None
    """
    try:
        adapter = get_adapter(platform)
        return adapter.adapt_content(
            title=title,
            content=content,
            images=images or [],
            videos=videos or [],
            hashtags=hashtags or [],
        )
    except ValueError:
        return None


__all__ = [
    "PLATFORM_ADAPTERS",
    "AdaptedContent",
    "BilibiliAdapter",
    "ContentConstraints",
    "ContentSpec",
    "DouyinAdapter",
    "LoginResult",
    # 基类和模型
    "PlatformAdapter",
    "PublishResult",
    "UserProfile",
    "WechatAdapter",
    # 适配器
    "XiaohongshuAdapter",
    "adapt_content_for_platform",
    # 工具函数
    "get_adapter",
    "get_platform_selector",
    "get_platform_spec",
    "get_platform_url",
    # 云端辅助函数
    "get_supported_platforms",
    "list_platforms",
]
