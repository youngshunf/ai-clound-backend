"""媒体生成异步任务"""

import httpx
from opendal import AsyncOperator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.encryption import key_encryption
from backend.app.llm.core.media_adapters.registry import get_adapter
from backend.app.llm.crud.crud_media_task import media_task_dao
from backend.app.llm.crud.crud_provider import provider_dao
from backend.app.llm.enums import MediaErrorCode, MediaTaskStatus
from backend.app.task.celery import celery_app
from backend.app.billing.service.credit_service import credit_service
from backend.common.log import log
from backend.database.db import async_db_session
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.utils.timezone import timezone

# 轮询配置
POLL_FAST_INTERVAL = 2      # 前 30 秒每 2 秒
POLL_SLOW_INTERVAL = 5      # 之后每 5 秒
POLL_FAST_DURATION = 30     # 快速轮询持续时间（秒）
POLL_TIMEOUT = 600          # 超时时间 10 分钟


@celery_app.task(name='media_poll_task', bind=True, max_retries=300)
async def media_poll_task(self, task_id: str) -> str:
    """轮询媒体生成任务状态

    退避策略：前 30s 每 2s，之后每 5s，超 10min 超时
    """
    async with async_db_session() as db:
        task = await media_task_dao.get_by_task_id(db, task_id)
        if not task:
            log.error(f'[MediaPoll] 任务不存在: {task_id}')
            return f'任务不存在: {task_id}'

        if task.status != MediaTaskStatus.PROCESSING:
            log.info(f'[MediaPoll] 任务已结束: {task_id}, status={task.status}')
            return f'任务已结束: {task.status}'

        # 超时检查
        elapsed = (timezone.now() - task.created_time).total_seconds()
        if elapsed > POLL_TIMEOUT:
            await media_task_dao.update_status(
                db, task_id,
                status=MediaTaskStatus.FAILED,
                error_code=MediaErrorCode.TIMEOUT,
                error_message=f'任务超时 ({POLL_TIMEOUT}s)',
            )
            await db.commit()
            # 退回预扣积分
            await _refund_credits(db, task)
            log.warning(f'[MediaPoll] 任务超时: {task_id}')
            return f'任务超时: {task_id}'

        # 获取适配器
        provider = await provider_dao.get(db, task.provider_id)
        if not provider:
            log.error(f'[MediaPoll] 供应商不存在: {task.provider_id}')
            return f'供应商不存在: {task.provider_id}'

        api_key = key_encryption.decrypt(provider.api_key_encrypted) if provider.api_key_encrypted else None
        adapter = get_adapter(
            provider.provider_type,
            task.media_type,
            api_base_url=provider.api_base_url,
            api_key=api_key,
        )

        # 轮询
        try:
            result = await adapter.poll(task.vendor_task_id)
        except Exception as e:
            log.error(f'[MediaPoll] 轮询异常: {task_id} - {e}')
            # 不立即失败，重试
            countdown = POLL_SLOW_INTERVAL
            self.retry(countdown=countdown)
            return f'轮询异常，重试中: {task_id}'

        # 更新进度
        poll_count = task.poll_count + 1

        if result.status == 'completed':
            now = timezone.now()
            await media_task_dao.update_status(
                db, task_id,
                status=MediaTaskStatus.COMPLETED,
                progress=100,
                vendor_urls=result.vendor_urls,
                completed_at=now,
                credits_cost=task.credits_pre_deducted,
                poll_count=poll_count,
            )
            await db.commit()
            log.info(f'[MediaPoll] 任务完成: {task_id}, polls={poll_count}')

            # 触发 OSS 转存
            media_oss_transfer_task.delay(task_id)

            # 触发积分结算
            media_settle_credits_task.delay(task_id)

            # 触发 Webhook
            if task.webhook_url:
                media_webhook_task.delay(task_id)

            return f'任务完成: {task_id}'

        if result.status == 'failed':
            await media_task_dao.update_status(
                db, task_id,
                status=MediaTaskStatus.FAILED,
                error_code=result.error_code,
                error_message=result.error_message,
                poll_count=poll_count,
            )
            await db.commit()
            # 退回预扣积分
            await _refund_credits(db, task)
            log.error(f'[MediaPoll] 任务失败: {task_id} - {result.error_message}')
            return f'任务失败: {task_id}'

        # 仍在处理中
        await media_task_dao.update_status(
            db, task_id,
            status=MediaTaskStatus.PROCESSING,
            progress=result.progress,
            poll_count=poll_count,
        )
        await db.commit()

        # 计算下次轮询间隔
        countdown = POLL_FAST_INTERVAL if elapsed < POLL_FAST_DURATION else POLL_SLOW_INTERVAL
        self.retry(countdown=countdown)
        return f'继续轮询: {task_id}, progress={result.progress}'


@celery_app.task(name='media_oss_transfer_task')
async def media_oss_transfer_task(task_id: str) -> str:
    """将厂商临时 URL 转存到自有 OSS"""
    async with async_db_session() as db:
        task = await media_task_dao.get_by_task_id(db, task_id)
        if not task or not task.vendor_urls:
            return f'无需转存: {task_id}'

        oss_urls = []
        for i, vendor_url in enumerate(task.vendor_urls):
            try:
                # 下载文件
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.get(vendor_url)
                    resp.raise_for_status()
                    content = resp.content

                # 确定文件扩展名
                content_type = resp.headers.get('content-type', '')
                ext = _guess_extension(content_type, task.media_type)

                # 上传到 OSS
                oss_path = f'media/{task.task_id}/{i}{ext}'
                oss_url = await _upload_to_oss(db, oss_path, content)
                oss_urls.append(oss_url)
                log.info(f'[MediaOSS] 转存成功: {oss_path}')

            except Exception as e:
                log.error(f'[MediaOSS] 转存失败: {task_id}/{i} - {e}')
                # 转存失败不影响任务状态，vendor_urls 作为降级
                continue

        if oss_urls:
            await media_task_dao.update_status(db, task_id, status=task.status, oss_urls=oss_urls)
            await db.commit()

        return f'转存完成: {task_id}, {len(oss_urls)}/{len(task.vendor_urls)}'


@celery_app.task(name='media_settle_credits_task')
async def media_settle_credits_task(task_id: str) -> str:
    """积分结算 — 预扣与实际差额退回"""
    async with async_db_session() as db:
        task = await media_task_dao.get_by_task_id(db, task_id)
        if not task:
            return f'任务不存在: {task_id}'

        if task.status != MediaTaskStatus.COMPLETED:
            return f'任务未完成: {task_id}'

        # 计算差额
        actual_cost = task.credits_cost
        pre_deducted = task.credits_pre_deducted
        diff = pre_deducted - actual_cost

        if diff > 0:
            # 退回多扣的积分
            await credit_service.add_credits(
                db, task.user_id, diff,
                transaction_type='refund',
                reference_id=task.task_id,
                reference_type='media_generation',
                description=f'媒体生成结算退回: {task.model_name}',
                is_purchased=False,
            )
            await db.commit()
            log.info(f'[MediaSettle] 退回差额: {task_id}, diff={diff}')

        return f'结算完成: {task_id}'


@celery_app.task(name='media_webhook_task', max_retries=3)
async def media_webhook_task(task_id: str) -> str:
    """Webhook 回调通知"""
    async with async_db_session() as db:
        task = await media_task_dao.get_by_task_id(db, task_id)
        if not task or not task.webhook_url:
            return f'无需回调: {task_id}'

        payload = {
            'id': task.task_id,
            'status': task.status,
            'media_type': task.media_type,
            'urls': task.oss_urls or task.vendor_urls,
            'error': {'code': task.error_code, 'message': task.error_message} if task.error_code else None,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    task.webhook_url,
                    json=payload,
                    headers={'Content-Type': 'application/json'},
                )
                resp.raise_for_status()
                log.info(f'[MediaWebhook] 回调成功: {task_id} -> {task.webhook_url}')
                return f'回调成功: {task_id}'
        except Exception as e:
            log.error(f'[MediaWebhook] 回调失败: {task_id} - {e}')
            return f'回调失败: {task_id}'


async def _refund_credits(db, task) -> None:
    """退回预扣积分"""
    if task.credits_pre_deducted > 0:
        await credit_service.add_credits(
            db, task.user_id, task.credits_pre_deducted,
            transaction_type='refund',
            reference_id=task.task_id,
            reference_type='media_generation',
            description=f'媒体生成失败退回: {task.model_name}',
            is_purchased=False,
        )
        await db.commit()
        log.info(f'[MediaRefund] 退回积分: {task.task_id}, amount={task.credits_pre_deducted}')


def _guess_extension(content_type: str, media_type: str) -> str:
    """根据 Content-Type 猜测文件扩展名"""
    ext_map = {
        'image/png': '.png',
        'image/jpeg': '.jpg',
        'image/webp': '.webp',
        'video/mp4': '.mp4',
        'video/webm': '.webm',
    }
    ext = ext_map.get(content_type.split(';')[0].strip())
    if ext:
        return ext
    return '.png' if media_type == 'image' else '.mp4'


async def _upload_to_oss(db: AsyncSession, path: str, content: bytes) -> str:
    """上传到 S3 兼容存储，返回访问 URL"""
    storages = await s3_storage_dao.get_all(db)
    if not storages:
        raise RuntimeError('S3 存储配置不存在，请先在管理后台配置存储')
    s3_storage = storages[0]

    op = AsyncOperator(
        's3',
        endpoint=s3_storage.endpoint,
        access_key_id=s3_storage.access_key,
        secret_access_key=s3_storage.secret_key,
        bucket=s3_storage.bucket,
        root=s3_storage.prefix or '/',
        region=s3_storage.region or 'any',
    )
    await op.write(path, content)

    # 构建访问 URL（优先 CDN）
    if s3_storage.cdn_domain:
        base_url = s3_storage.cdn_domain.rstrip('/')
        if s3_storage.prefix:
            prefix = s3_storage.prefix.strip('/')
            return f'{base_url}/{prefix}/{path}'
        return f'{base_url}/{path}'

    bucket_path = f'/{s3_storage.bucket}'
    if s3_storage.prefix:
        prefix = s3_storage.prefix if s3_storage.prefix.startswith('/') else f'/{s3_storage.prefix}'
        return f'{s3_storage.endpoint}{bucket_path}{prefix}/{path}'
    return f'{s3_storage.endpoint}{bucket_path}/{path}'
