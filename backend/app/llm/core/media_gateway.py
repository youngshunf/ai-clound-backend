"""媒体生成网关"""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.circuit_breaker import circuit_breaker_manager
from backend.app.llm.core.encryption import key_encryption
from backend.app.llm.core.media_adapters.base import MediaRequest, SubmitResult
from backend.app.llm.core.media_adapters.registry import get_adapter
from backend.app.llm.core.rate_limiter import rate_limiter
from backend.app.llm.crud.crud_media_task import media_task_dao
from backend.app.llm.crud.crud_model_config import model_config_dao
from backend.app.llm.crud.crud_provider import provider_dao
from backend.app.llm.enums import MediaErrorCode, MediaTaskStatus, MediaType
from backend.app.llm.model.media_task import MediaTask
from backend.app.billing.service.credit_service import credit_service
from backend.common.exception.errors import HTTPError
from backend.common.log import log
from backend.utils.timezone import timezone


class MediaGatewayError(HTTPError):
    """媒体网关错误"""

    def __init__(self, message: str, code: int = 500, error_code: str | None = None) -> None:
        super().__init__(code=code, msg=message)
        self.error_code = error_code or MediaErrorCode.VENDOR_ERROR


def _generate_task_id(media_type: str) -> str:
    """生成任务 ID"""
    prefix = 'img' if media_type == MediaType.IMAGE else 'vid'
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


class MediaGateway:
    """媒体生成网关 — 核心流水线"""

    async def generate(
        self,
        db: AsyncSession,
        *,
        model_name: str,
        media_type: str,
        request: MediaRequest,
        user_id: int,
        api_key_id: int,
        rpm_limit: int,
        ip_address: str | None = None,
    ) -> dict:
        """
        统一生成入口

        流水线：解析模型 → 熔断检查 → 限流 → 预扣积分 → 创建任务 → submit
        """
        # 1. 解析模型配置
        model_config = await model_config_dao.get_by_name_and_type(db, model_name, media_type)
        if not model_config:
            # 尝试模糊匹配
            model_config = await model_config_dao.select_model_by_column(
                db, model_name=model_name, enabled=True
            )
        if not model_config:
            raise MediaGatewayError(f'模型不存在: {model_name}', code=404, error_code=MediaErrorCode.MODEL_UNAVAILABLE)

        # 2. 获取供应商
        provider = await provider_dao.get(db, model_config.provider_id)
        if not provider or not provider.enabled:
            raise MediaGatewayError(f'供应商不可用: {model_config.provider_id}', code=503, error_code=MediaErrorCode.MODEL_UNAVAILABLE)

        # 3. 熔断检查
        breaker = circuit_breaker_manager.get_breaker(f'media:{provider.provider_type}:{model_name}')
        if not breaker.allow_request():
            raise MediaGatewayError(f'供应商熔断中: {provider.name}', code=503, error_code=MediaErrorCode.MODEL_UNAVAILABLE)

        # 4. 限流
        await rate_limiter.check_rpm(api_key_id, rpm_limit)

        # 5. 预估积分并预扣
        cost_per_gen = getattr(model_config, 'cost_per_generation', None)
        cost_per_sec = getattr(model_config, 'cost_per_second', None)
        adapter = get_adapter(
            provider.provider_type,
            media_type,
            api_base_url=provider.api_base_url,
            api_key=key_encryption.decrypt(provider.api_key_encrypted) if provider.api_key_encrypted else None,
        )
        estimated_credits = adapter.estimate_credits(request, cost_per_gen, cost_per_sec)

        await credit_service.check_credits(db, user_id, estimated_credits)
        await credit_service.deduct_credits(
            db,
            user_id,
            estimated_credits,
            reference_type='media_generation',
            description=f'媒体生成预扣: {model_name}',
        )

        # 6. 创建任务记录
        task_id = _generate_task_id(media_type)
        task = MediaTask(
            task_id=task_id,
            user_id=user_id,
            api_key_id=api_key_id,
            model_name=model_name,
            provider_id=provider.id,
            media_type=media_type,
            prompt=request.prompt,
            status=MediaTaskStatus.PENDING,
            params=request.extra,
            webhook_url=request.webhook_url,
            credits_pre_deducted=estimated_credits,
            ip_address=ip_address,
        )
        task = await media_task_dao.create(db, task)

        # 7. 调用适配器 submit
        try:
            result = await adapter.submit(request)
            breaker.record_success()
        except Exception as e:
            breaker.record_failure()
            # 更新任务为失败
            error_code = getattr(e, 'error_code', MediaErrorCode.VENDOR_ERROR)
            error_message = getattr(e, 'message', str(e))
            await media_task_dao.update_status(
                db, task_id,
                status=MediaTaskStatus.FAILED,
                error_code=error_code,
                error_message=error_message,
            )
            # 退回预扣积分
            await credit_service.add_credits(
                db, user_id, estimated_credits,
                transaction_type='refund',
                reference_type='media_generation',
                description=f'媒体生成失败退回: {model_name}',
                is_purchased=False,
            )
            await db.commit()
            raise MediaGatewayError(error_message, code=500, error_code=error_code) from e

        # 8. 处理结果
        if result.is_async:
            # 异步任务：更新 vendor_task_id，启动轮询
            await media_task_dao.update_status(
                db, task_id,
                status=MediaTaskStatus.PROCESSING,
                vendor_task_id=result.vendor_task_id,
            )
            await db.commit()

            # 触发 Celery 轮询任务
            from backend.app.llm.tasks.tasks import media_poll_task
            media_poll_task.delay(task_id)

            log.info(f'[MediaGateway] 异步任务已提交: {task_id}, vendor={result.vendor_task_id}')
            return {
                'task_id': task_id,
                'status': MediaTaskStatus.PROCESSING,
                'progress': 0,
                'estimated_seconds': result.estimated_seconds,
            }
        else:
            # 同步任务：直接完成
            now = timezone.now()
            await media_task_dao.update_status(
                db, task_id,
                status=MediaTaskStatus.COMPLETED,
                progress=100,
                vendor_urls=result.vendor_urls,
                credits_cost=estimated_credits,
                completed_at=now,
            )
            await db.commit()

            # 触发 OSS 转存
            from backend.app.llm.tasks.tasks import media_oss_transfer_task
            media_oss_transfer_task.delay(task_id)

            log.info(f'[MediaGateway] 同步任务完成: {task_id}, urls={len(result.vendor_urls or [])}')
            return {
                'task_id': task_id,
                'status': MediaTaskStatus.COMPLETED,
                'vendor_urls': result.vendor_urls,
                'revised_prompt': result.revised_prompt,
                'credits_cost': float(estimated_credits),
            }

    async def get_task(self, db: AsyncSession, task_id: str) -> MediaTask | None:
        """查询任务状态"""
        return await media_task_dao.get_by_task_id(db, task_id)


media_gateway = MediaGateway()
