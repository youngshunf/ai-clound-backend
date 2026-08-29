"""网页发布物化 worker（bundle-zip 对象存储 fan-out 异步化，2026-08-29）。

背景：POST /api/v1/publish/*/sites 曾在请求内同步物化 bundle-zip（读 zip + 逐对象串行 PUT
七牛），21MB 包实测 38-39s，超过 daemon reqwest 写死的 30s 总超时——客户端 499 放弃、
服务端 200 落库、发布结果丢失，非幂等重试一晚重复发布 4 次。现在请求侧只落
pending revision 立即返回，本模块在 worker 里完成物化并翻转 current_revision_id。

- `publish_materialize_revision`：单 revision 物化。确定性业务失败（制品缺失、zip 损坏、
  referenced 资产失效）在 service 内落 `failed` + 主人可读文案并通知主人，不抛出；
  意外异常（对象存储/网络/DB 抖动）由本任务退避重试，耗尽后用独立会话如实落 `failed`
  （零 fake：revision 不许永远停在 pending）。
- `publish_materialize_sweep`：每分钟兜底——after_commit 派发失败 / worker 中断 / broker
  抖动留下的滞留 pending 重新入队。物化任务本身幂等（SKIP LOCKED + 状态判据），重复
  入队无害。
"""

from __future__ import annotations

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from backend.app.hasn_publish.service.publish_service import publish_service
from backend.common.log import log
from backend.database.db import async_db_session

#: 意外异常的重试间隔（秒）：对象存储/网络抖动的典型恢复尺度
_RETRY_COUNTDOWN_SECONDS = 60

#: 重试耗尽后落库的主人可读文案（异常细节进日志，不给主人看堆栈）
_EXHAUSTED_MSG = '发布处理失败（对象存储暂不可达），已自动重试多次；请稍后重新发布'


@shared_task(bind=True, name='publish_materialize_revision', max_retries=3)
async def publish_materialize_revision(self, revision_id: int) -> str:  # noqa: ANN001
    """物化单个 pending revision（同步/异步边界与失败语义见 service.materialize_revision）。"""
    try:
        async with async_db_session.begin() as db:
            return await publish_service.materialize_revision(db, revision_id=revision_id)
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=_RETRY_COUNTDOWN_SECONDS)
        except MaxRetriesExceededError:
            log.exception(f'[Publish] 物化重试耗尽: revision_id={revision_id}, err={exc}')
            try:
                async with async_db_session.begin() as db:
                    await publish_service.mark_materialize_failed(db, revision_id=revision_id, error=_EXHAUSTED_MSG)
            except Exception as mark_exc:
                # 落 failed 也失败（DB 抖动）：保持 pending 交给每分钟 sweep 重派，不吞错
                log.exception(
                    f'[Publish] 落 failed 失败，等 sweep 兜底: revision_id={revision_id}, err={mark_exc}'
                )
            raise exc


@shared_task(name='publish_materialize_sweep')
async def publish_materialize_sweep() -> str:
    """每分钟兜底：把滞留 pending 的 revision 重新入队（正常路径秒级被 worker 捡走，本任务应常年空转）。"""
    async with async_db_session() as db:
        stuck_ids = await publish_service.find_stuck_pending_materializations(db)
    enqueued = 0
    for revision_id in stuck_ids:
        try:
            publish_materialize_revision.delay(revision_id)
            enqueued += 1
        except Exception as exc:
            log.warning(f'[Publish] sweep 重派失败（下一分钟再试）: revision_id={revision_id}, err={exc}')
    if stuck_ids:
        # 有滞留说明前面的派发链路出过事——warn 可见；空转是正常态，不打日志（每分钟一条会淹掉真日志）
        log.warning(f'[Publish] sweep 捞回滞留物化: {enqueued}/{len(stuck_ids)} 重新入队')
    return f'redispatched:{enqueued}/{len(stuck_ids)}'
