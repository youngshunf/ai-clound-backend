"""媒体生成服务 — 编排层"""

import time

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.media_adapters.base import MediaRequest
from backend.app.llm.core.media_gateway import media_gateway
from backend.app.llm.enums import MediaType
from backend.app.llm.schema.image import ImageDataItem, ImageGenerationRequest, ImageGenerationResponse, ImageUsage
from backend.app.llm.schema.video import VideoDataItem, VideoGenerationRequest, VideoGenerationResponse, VideoUsage
from backend.app.newapi.apikey.service import api_key_service
from backend.common.exception import errors
from backend.common.log import log


class MediaService:
    """媒体生成服务"""

    async def generate_image(
        self,
        db: AsyncSession,
        *,
        api_key: str,
        request: ImageGenerationRequest,
        ip_address: str | None = None,
    ) -> ImageGenerationResponse:
        """图像生成"""
        api_key_record = await api_key_service.verify_api_key(db, api_key)
        rate_limits = await api_key_service.get_rate_limits(db, api_key_record)

        media_request = MediaRequest(
            prompt=request.prompt,
            model=request.model,
            n=request.n,
            size=request.size,
            quality=request.quality,
            style=request.style,
            webhook_url=request.webhook_url,
        )

        result = await media_gateway.generate(
            db,
            model_name=request.model,
            media_type=MediaType.IMAGE,
            request=media_request,
            user_id=api_key_record.user_id,
            api_key_id=api_key_record.id,
            rpm_limit=rate_limits['rpm_limit'],
            ip_address=ip_address,
        )

        # 构建响应
        if result['status'] == 'completed':
            data = [
                ImageDataItem(url=url, revised_prompt=result.get('revised_prompt'))
                for url in (result.get('vendor_urls') or [])
            ]
            return ImageGenerationResponse(
                id=result['task_id'],
                status='completed',
                data=data,
                usage=ImageUsage(credits=result.get('credits_cost', 0)),
                created=int(time.time()),
            )
        else:
            # 异步任务
            return ImageGenerationResponse(
                id=result['task_id'],
                status=result['status'],
                progress=result.get('progress', 0),
                created=int(time.time()),
            )

    async def generate_video(
        self,
        db: AsyncSession,
        *,
        api_key: str,
        request: VideoGenerationRequest,
        ip_address: str | None = None,
    ) -> VideoGenerationResponse:
        """视频生成"""
        api_key_record = await api_key_service.verify_api_key(db, api_key)
        rate_limits = await api_key_service.get_rate_limits(db, api_key_record)

        media_request = MediaRequest(
            prompt=request.prompt,
            model=request.model,
            duration=request.duration,
            aspect_ratio=request.aspect_ratio,
            mode=request.mode,
            cfg_scale=request.cfg_scale,
            image_url=request.image_url,
            webhook_url=request.webhook_url,
        )

        result = await media_gateway.generate(
            db,
            model_name=request.model,
            media_type=MediaType.VIDEO,
            request=media_request,
            user_id=api_key_record.user_id,
            api_key_id=api_key_record.id,
            rpm_limit=rate_limits['rpm_limit'],
            ip_address=ip_address,
        )

        return VideoGenerationResponse(
            id=result['task_id'],
            status=result['status'],
            progress=result.get('progress', 0),
            estimated_seconds=result.get('estimated_seconds'),
            created=int(time.time()),
        )

    async def get_image_task(
        self,
        db: AsyncSession,
        task_id: str,
    ) -> ImageGenerationResponse:
        """查询图像任务状态"""
        task = await media_gateway.get_task(db, task_id)
        if not task:
            raise errors.NotFoundError(msg=f'任务不存在: {task_id}')

        data = None
        usage = None
        if task.status == 'completed':
            urls = task.oss_urls or task.vendor_urls or []
            data = [ImageDataItem(url=url) for url in urls]
            usage = ImageUsage(credits=float(task.credits_cost))

        error = None
        if task.status == 'failed':
            error = {'code': task.error_code, 'message': task.error_message}

        return ImageGenerationResponse(
            id=task.task_id,
            status=task.status,
            data=data,
            usage=usage,
            progress=task.progress,
            created=int(task.created_time.timestamp()),
            error=error,
        )

    async def get_video_task(
        self,
        db: AsyncSession,
        task_id: str,
    ) -> VideoGenerationResponse:
        """查询视频任务状态"""
        task = await media_gateway.get_task(db, task_id)
        if not task:
            raise errors.NotFoundError(msg=f'任务不存在: {task_id}')

        data = None
        usage = None
        if task.status == 'completed':
            urls = task.oss_urls or task.vendor_urls or []
            url = urls[0] if urls else None
            data = VideoDataItem(url=url)
            usage = VideoUsage(credits=float(task.credits_cost))

        error = None
        if task.status == 'failed':
            error = {'code': task.error_code, 'message': task.error_message}

        return VideoGenerationResponse(
            id=task.task_id,
            status=task.status,
            progress=task.progress,
            data=data,
            usage=usage,
            created=int(task.created_time.timestamp()),
            error=error,
        )


media_service = MediaService()
