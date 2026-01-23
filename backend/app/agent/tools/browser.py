"""
云端浏览器工具 - 使用浏览器池执行发布操作

@author Ysf
@date 2025-12-28
"""

from typing import Any

from agent_core.runtime.context import RuntimeContext
from agent_core.runtime.interfaces import RuntimeType
from agent_core.tools.base import (
    ToolCapability,
    ToolInterface,
    ToolMetadata,
    ToolResult,
)

# 从本地 platforms 模块导入（内部转发自 agent-core）
from backend.app.platforms import (
    adapt_content_for_platform,
    get_adapter,
    list_platforms,
)


class CloudBrowserPublishTool(ToolInterface):
    """
    云端浏览器发布工具

    使用云端浏览器池执行发布操作。
    需要用户开启凭证同步功能。
    平台配置从 agent-core 的 YAML 文件加载。
    """

    metadata = ToolMetadata(
        name="browser_publish",
        description="使用云端浏览器池发布内容到社交平台",
        capabilities=[ToolCapability.BROWSER_AUTOMATION],
        supported_runtimes=[RuntimeType.CLOUD],
    )

    async def execute(
        self,
        ctx: RuntimeContext,
        *,
        platform: str,
        account_id: str,
        content: dict[str, Any],
    ) -> ToolResult:
        """
        执行云端浏览器发布

        Args:
            ctx: 运行时上下文
            platform: 目标平台
            account_id: 账号ID
            content: 发布内容

        Returns:
            ToolResult: 执行结果
        """
        try:
            # 验证平台是否支持
            supported = list_platforms()
            if platform not in supported:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"不支持的平台: {platform}，支持的平台: {supported}",
                )

            # 获取平台适配器
            get_adapter(platform)

            # 获取浏览器池
            browser_pool = ctx.extra.get("browser_pool")
            if not browser_pool:
                return ToolResult(
                    success=False,
                    data=None,
                    error="浏览器池未配置",
                )

            # 获取用户同步的凭证
            credential = await self._get_synced_credential(
                ctx, platform, account_id
            )
            if not credential:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"未找到同步的凭证: {platform}/{account_id}，请在桌面端开启凭证同步",
                )

            # 使用平台适配器适配内容
            adapted_content = adapt_content_for_platform(
                platform=platform,
                title=content.get("title", ""),
                content=content.get("content", ""),
                images=content.get("images", []),
                videos=content.get("videos", []),
                hashtags=content.get("hashtags", []),
            )
            if not adapted_content:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"内容适配失败: {platform}",
                )

            # 从浏览器池获取实例
            browser_context = await browser_pool.acquire(platform, credential)

            try:
                # 执行发布（使用适配后的内容）
                publish_content = {
                    "title": adapted_content.title,
                    "content": adapted_content.content,
                    "images": adapted_content.images,
                    "hashtags": adapted_content.hashtags,
                }
                result = await browser_context.publish(publish_content)

                return ToolResult(
                    success=True,
                    data={
                        "platform": platform,
                        "account_id": account_id,
                        "adapted_content": publish_content,
                        "warnings": adapted_content.warnings,
                        "result": result,
                    },
                )

            finally:
                # 释放浏览器实例
                await browser_pool.release(browser_context)

        except Exception as e:
            return ToolResult(
                success=False,
                data=None,
                error=f"云端发布失败: {e!s}",
            )

    async def _get_synced_credential(
        self,
        ctx: RuntimeContext,
        platform: str,
        account_id: str,
    ) -> dict[str, Any] | None:
        """
        获取用户同步的凭证

        Args:
            ctx: 运行时上下文
            platform: 平台名称
            account_id: 账号ID

        Returns:
            凭证数据或 None
        """
        db = ctx.extra.get("db")
        if not db:
            return None

        # TODO: 从数据库查询用户同步的凭证
        # 需要实现凭证同步功能后完善
        return None

    def get_schema(self) -> dict[str, Any]:
        """获取工具参数 Schema，平台列表从 agent-core 动态获取"""
        # 从 agent-core 获取支持的平台列表
        supported_platforms = list_platforms()

        return {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "目标平台",
                    "enum": supported_platforms,
                },
                "account_id": {
                    "type": "string",
                    "description": "账号ID",
                },
                "content": {
                    "type": "object",
                    "description": "发布内容 {title, content, images, videos, hashtags}",
                    "properties": {
                        "title": {"type": "string", "description": "标题"},
                        "content": {"type": "string", "description": "正文"},
                        "images": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "图片路径列表",
                        },
                        "videos": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "视频路径列表",
                        },
                        "hashtags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "话题标签列表",
                        },
                    },
                },
            },
            "required": ["platform", "account_id", "content"],
        }
