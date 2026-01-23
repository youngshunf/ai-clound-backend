"""
云端浏览器池管理

提供 Playwright 浏览器实例池化管理，支持：
- 实例复用
- 平台隔离
- 自动清理
- 健康检查

@author Ysf
@date 2025-12-28
"""

import asyncio
import logging

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from playwright.async_api import Browser, BrowserContext, async_playwright

logger = logging.getLogger(__name__)


class BrowserStatus(StrEnum):
    """浏览器状态"""
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


@dataclass
class BrowserInstance:
    """浏览器实例"""
    id: str
    platform: str
    context: BrowserContext
    status: BrowserStatus = BrowserStatus.IDLE
    created_at: datetime = field(default_factory=datetime.now)
    last_used_at: datetime = field(default_factory=datetime.now)
    use_count: int = 0
    error_count: int = 0


@dataclass
class PoolConfig:
    """浏览器池配置"""
    max_instances: int = 10
    max_instances_per_platform: int = 3
    idle_timeout: int = 300  # 秒
    max_use_count: int = 50
    max_error_count: int = 3
    cleanup_interval: int = 60  # 秒
    headless: bool = True


class BrowserPool:
    """
    浏览器池管理器

    管理 Playwright 浏览器实例，提供：
    - 实例获取和释放
    - 平台隔离
    - 自动清理过期实例
    - 健康检查
    """

    def __init__(self, config: PoolConfig | None = None) -> None:
        """
        初始化浏览器池

        Args:
            config: 池配置
        """
        self._config = config or PoolConfig()
        self._playwright = None
        self._browser: Browser | None = None
        self._instances: dict[str, BrowserInstance] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._running = False
        self._instance_counter = 0

    async def start(self) -> None:
        """启动浏览器池"""
        if self._running:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        self._running = True

        # 启动清理任务
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("浏览器池已启动")

    async def stop(self) -> None:
        """停止浏览器池"""
        if not self._running:
            return

        self._running = False

        # 停止清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 关闭所有实例
        async with self._lock:
            for instance in list(self._instances.values()):
                await self._close_instance(instance)
            self._instances.clear()

        # 关闭浏览器
        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.info("浏览器池已停止")

    async def acquire(
        self,
        platform: str,
        credential: dict[str, Any] | None = None,
    ) -> BrowserInstance:
        """
        获取浏览器实例

        Args:
            platform: 平台名称
            credential: 凭证数据（包含 cookies 等）

        Returns:
            浏览器实例
        """
        if not self._running:
            raise RuntimeError("浏览器池未启动")

        async with self._lock:
            # 查找空闲实例
            for instance in self._instances.values():
                if (
                    instance.platform == platform
                    and instance.status == BrowserStatus.IDLE
                    and instance.error_count < self._config.max_error_count
                    and instance.use_count < self._config.max_use_count
                ):
                    instance.status = BrowserStatus.BUSY
                    instance.last_used_at = datetime.now()
                    instance.use_count += 1
                    logger.debug(f"复用浏览器实例: {instance.id}")
                    return instance

            # 检查是否可以创建新实例
            platform_count = sum(
                1 for i in self._instances.values() if i.platform == platform
            )
            if platform_count >= self._config.max_instances_per_platform:
                raise RuntimeError(f"平台 {platform} 实例数已达上限")

            if len(self._instances) >= self._config.max_instances:
                raise RuntimeError("浏览器池实例数已达上限")

            # 创建新实例
            instance = await self._create_instance(platform, credential)
            self._instances[instance.id] = instance
            logger.info(f"创建新浏览器实例: {instance.id}")
            return instance

    async def release(self, instance: BrowserInstance, error: bool = False) -> None:
        """
        释放浏览器实例

        Args:
            instance: 浏览器实例
            error: 是否发生错误
        """
        async with self._lock:
            if instance.id not in self._instances:
                return

            if error:
                instance.error_count += 1
                instance.status = BrowserStatus.ERROR
                logger.warning(f"浏览器实例出错: {instance.id}, 错误次数: {instance.error_count}")

                # 错误次数过多，关闭实例
                if instance.error_count >= self._config.max_error_count:
                    await self._close_instance(instance)
                    del self._instances[instance.id]
                    logger.info(f"关闭错误实例: {instance.id}")
            else:
                instance.status = BrowserStatus.IDLE
                instance.last_used_at = datetime.now()

    @asynccontextmanager
    async def get_context(
        self,
        platform: str,
        credential: dict[str, Any] | None = None,
    ):
        """
        上下文管理器方式获取浏览器实例

        Args:
            platform: 平台名称
            credential: 凭证数据

        Yields:
            浏览器实例
        """
        instance = await self.acquire(platform, credential)
        error = False
        try:
            yield instance
        except Exception:
            error = True
            raise
        finally:
            await self.release(instance, error=error)

    async def _create_instance(
        self,
        platform: str,
        credential: dict[str, Any] | None = None,
    ) -> BrowserInstance:
        """创建浏览器实例"""
        self._instance_counter += 1
        instance_id = f"{platform}_{self._instance_counter}"

        # 创建浏览器上下文
        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )

        # 注入凭证
        if credential:
            cookies = credential.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)

            storage_state = credential.get("storage_state")
            if storage_state:
                # 注入 localStorage
                for origin, data in storage_state.get("origins", {}).items():
                    page = await context.new_page()
                    await page.goto(origin)
                    for key, value in data.get("localStorage", {}).items():
                        await page.evaluate(
                            f"localStorage.setItem('{key}', '{value}')"
                        )
                    await page.close()

        return BrowserInstance(
            id=instance_id,
            platform=platform,
            context=context,
            status=BrowserStatus.BUSY,
        )

    async def _close_instance(self, instance: BrowserInstance) -> None:
        """关闭浏览器实例"""
        try:
            await instance.context.close()
        except Exception as e:
            logger.error(f"关闭浏览器实例失败: {instance.id}, {e}")

    async def _cleanup_loop(self) -> None:
        """清理循环"""
        while self._running:
            try:
                await asyncio.sleep(self._config.cleanup_interval)
                await self._cleanup_idle_instances()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理任务出错: {e}")

    async def _cleanup_idle_instances(self) -> None:
        """清理空闲实例"""
        now = datetime.now()
        timeout = timedelta(seconds=self._config.idle_timeout)

        async with self._lock:
            to_remove = []
            for instance_id, instance in self._instances.items():
                if (
                    instance.status == BrowserStatus.IDLE
                    and now - instance.last_used_at > timeout
                ):
                    to_remove.append(instance_id)

            for instance_id in to_remove:
                instance = self._instances.pop(instance_id)
                await self._close_instance(instance)
                logger.info(f"清理空闲实例: {instance_id}")

    async def health_check(self) -> dict[str, Any]:
        """
        健康检查

        Returns:
            健康状态信息
        """
        async with self._lock:
            total = len(self._instances)
            idle = sum(1 for i in self._instances.values() if i.status == BrowserStatus.IDLE)
            busy = sum(1 for i in self._instances.values() if i.status == BrowserStatus.BUSY)
            error = sum(1 for i in self._instances.values() if i.status == BrowserStatus.ERROR)

            platforms = {}
            for instance in self._instances.values():
                if instance.platform not in platforms:
                    platforms[instance.platform] = {"total": 0, "idle": 0, "busy": 0}
                platforms[instance.platform]["total"] += 1
                if instance.status == BrowserStatus.IDLE:
                    platforms[instance.platform]["idle"] += 1
                elif instance.status == BrowserStatus.BUSY:
                    platforms[instance.platform]["busy"] += 1

            return {
                "running": self._running,
                "total_instances": total,
                "idle_instances": idle,
                "busy_instances": busy,
                "error_instances": error,
                "max_instances": self._config.max_instances,
                "platforms": platforms,
            }

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            "total_created": self._instance_counter,
            "current_instances": len(self._instances),
            "config": {
                "max_instances": self._config.max_instances,
                "max_per_platform": self._config.max_instances_per_platform,
                "idle_timeout": self._config.idle_timeout,
            },
        }


# 全局浏览器池实例
_browser_pool: BrowserPool | None = None


async def get_browser_pool() -> BrowserPool:
    """获取全局浏览器池实例"""
    global _browser_pool
    if _browser_pool is None:
        _browser_pool = BrowserPool()
        await _browser_pool.start()
    return _browser_pool


async def shutdown_browser_pool() -> None:
    """关闭全局浏览器池"""
    global _browser_pool
    if _browser_pool:
        await _browser_pool.stop()
        _browser_pool = None
