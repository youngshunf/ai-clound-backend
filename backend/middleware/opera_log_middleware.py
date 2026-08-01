import json
import time

from asyncio import Queue
from typing import Any

from fastapi import Response
from starlette.datastructures import UploadFile
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.app.admin.schema.opera_log import CreateOperaLogParam
from backend.app.admin.service.opera_log_service import opera_log_service
from backend.common.context import ctx
from backend.common.enums import StatusType
from backend.common.log import log
from backend.common.observability.prometheus import (
    PROMETHEUS_APP_NAME,
    PROMETHEUS_EXCEPTION_COUNTER,
    PROMETHEUS_REQUEST_COST_TIME_HISTOGRAM,
    PROMETHEUS_REQUEST_IN_PROGRESS_GAUGE,
    PROMETHEUS_RESPONSE_COUNTER,
)
from backend.common.queue import batch_dequeue
from backend.common.response.response_code import StandardResponseCode
from backend.core.conf import settings
from backend.database.db import async_db_session
from backend.utils.trace_id import get_request_trace_id


class OperaLogMiddleware(BaseHTTPMiddleware):
    """操作日志中间件"""

    opera_log_queue: Queue = Queue(maxsize=settings.OPERA_LOG_QUEUE_MAXSIZE)

    @staticmethod
    def is_request_details_suppressed(path: str) -> bool:
        """判断请求是否包含禁止进入普通日志的敏感字段。"""
        return any(path.startswith(prefix) for prefix in settings.OPERA_LOG_REQUEST_DETAILS_PREFIX_EXCLUDE)

    async def dispatch(self, request: Request, call_next: Any) -> Response:  # noqa: C901
        """
        处理请求并记录操作日志

        :param request: FastAPI 请求对象
        :param call_next: 下一个中间件或路由处理函数
        :return:
        """
        path = request.url.path
        method = request.method
        suppress_request_details = self.is_request_details_suppressed(path)
        # 必须在读取 body 前判定；否则即使后续不入库，PII 仍会进入 args 和调试日志。
        args = None if suppress_request_details else await self.get_request_args(request)
        code = 200
        msg = 'Success'
        status = StatusType.enable
        elapsed = 0.0

        try:
            username = request.user.username
        except AttributeError:
            username = None

        should_log_opera = (
            path.startswith(settings.FASTAPI_API_V1_PATH)
            and path not in settings.OPERA_LOG_PATH_EXCLUDE
            and not suppress_request_details
        )

        try:
            response = await call_next(request)
        except Exception as e:
            elapsed = round((time.perf_counter() - ctx.perf_time) * 1000, 3)
            log.error(f'请求异常: {e!s}')

            if should_log_opera:
                code = getattr(e, 'code', StandardResponseCode.HTTP_500)
                msg = getattr(e, 'msg', str(e))
                status = StatusType.disable

            if path.startswith(settings.FASTAPI_API_V1_PATH):
                PROMETHEUS_EXCEPTION_COUNTER.labels(
                    app_name=PROMETHEUS_APP_NAME,
                    method=method,
                    path=path,
                    exception_type=type(e).__name__,
                ).inc()

            raise
        else:
            elapsed = round((time.perf_counter() - ctx.perf_time) * 1000, 3)

            if should_log_opera:
                # 检查上下文中的异常信息
                for exception_key in [
                    '__request_http_exception__',
                    '__request_validation_exception__',
                    '__request_assertion_error__',
                    '__request_custom_exception__',
                ]:
                    exception = ctx.get(exception_key)
                    if exception:
                        code = exception.get('code')
                        msg = exception.get('msg')
                        status = StatusType.disable
                        # 4xx 客户端错误（429 限频 / 404 / 409 / 422 校验等）是预期的客户端侧
                        # 状况，不是服务端故障——记 WARNING，避免刷爆后端 error 日志（曾出现
                        # diag 上报限频 429 被当 ERROR「请求异常」海量刷屏）。仅 5xx / 未知记 ERROR。
                        if isinstance(code, int) and 400 <= code < 500:
                            log.warning(f'请求异常: {msg}')
                        else:
                            log.error(f'请求异常: {msg}')
                        break

            if path.startswith(settings.FASTAPI_API_V1_PATH):
                PROMETHEUS_REQUEST_COST_TIME_HISTOGRAM.labels(
                    app_name=PROMETHEUS_APP_NAME, method=method, path=path
                ).observe(amount=elapsed, exemplar={'TraceID': get_request_trace_id()})
        finally:
            # summary 只能在请求后获取
            route = request.scope.get('route')
            summary = route.summary or '' if route else ''

            log.debug(f'接口摘要：[{summary}]')
            request_ip = '[REDACTED]' if suppress_request_details else ctx.ip
            request_args = '[REDACTED]' if suppress_request_details else args
            log.debug(f'请求地址：[{request_ip}]')
            log.debug(f'请求参数：{request_args}')

            if request.method != 'OPTIONS':
                log.debug('<-- 请求结束')

            if path.startswith(settings.FASTAPI_API_V1_PATH):
                log.info(f'{request_ip: <15} | {method: <8} | {code!s: <6} | {path} | {elapsed:.3f}ms')

            if should_log_opera and request.method != 'OPTIONS':
                opera_log_in = CreateOperaLogParam(
                    trace_id=get_request_trace_id(),
                    username=username,
                    method=method,
                    title=summary,
                    path=path,
                    ip=ctx.ip,
                    country=ctx.country,
                    region=ctx.region,
                    city=ctx.city,
                    user_agent=ctx.user_agent,
                    os=ctx.os,
                    browser=ctx.browser,
                    device=ctx.device,
                    args=args,
                    status=status,
                    code=str(code),
                    msg=msg,
                    cost_time=elapsed,
                    opera_time=ctx.start_time,
                )
                await self.opera_log_queue.put(opera_log_in)

            if path.startswith(settings.FASTAPI_API_V1_PATH):
                PROMETHEUS_RESPONSE_COUNTER.labels(
                    app_name=PROMETHEUS_APP_NAME, method=method, path=path, status_code=code
                ).inc()
                PROMETHEUS_REQUEST_IN_PROGRESS_GAUGE.labels(
                    app_name=PROMETHEUS_APP_NAME, method=method, path=path
                ).dec()

        return response

    async def get_request_args(self, request: Request) -> dict[str, Any] | None:  # noqa: C901
        """
        获取请求参数

        :param request: FastAPI 请求对象
        :return:
        """
        args: dict[str, Any] = {}

        # 查询参数
        query_params = dict(request.query_params)
        if query_params:
            args['query_params'] = self.desensitization(query_params)

        # 路径参数
        path_params = request.path_params
        if path_params:
            args['path_params'] = self.desensitization(path_params)

        # Tip: .body() 必须在 .form() 之前获取
        # https://github.com/encode/starlette/discussions/1933
        content_type = request.headers.get('Content-Type', '').split(';')

        # 大文件上传（模型包 / 引擎包动辄数百 MB）不得读进操作日志：`body()` 会把整个请求体
        # 缓冲进内存，非 JSON 分支还会再 decode 出一份等长字符串，随后的 `form()` 又要再解析一遍。
        # 这一切发生在路由之前，端点侧的分块读取与提前释放事务因此完全失效，单次发布就能把
        # worker 撑爆。这里只记录形状，不记录内容。
        try:
            content_length = int(request.headers.get('Content-Length') or 0)
        except ValueError:
            content_length = 0
        if 'multipart/form-data' in content_type or content_length > settings.OPERA_LOG_MAX_BODY_BYTES:
            args['data'] = f'[BODY OMITTED: {content_type[0] or "unknown"}, {content_length} bytes]'
            return args

        # 请求体
        body_data = await request.body()
        if body_data:
            # 注意：非 json 数据默认使用 data 作为键
            if 'application/json' not in content_type:
                args['data'] = body_data.decode('utf-8', 'ignore') if isinstance(body_data, bytes) else str(body_data)
            else:
                json_data = await request.json()
                if isinstance(json_data, dict):
                    args['json'] = self.desensitization(json_data)
                else:
                    args['data'] = str(json_data)

        # 表单参数
        form_data = await request.form()
        if len(form_data) > 0:
            serialized_form: dict[str, Any] = {}
            for k, v in form_data.items():
                if isinstance(v, UploadFile):
                    serialized_form[k] = {
                        'filename': v.filename,
                        'content_type': v.content_type,
                        'size': v.size,
                    }
                else:
                    serialized_form[k] = v
            if 'multipart/form-data' not in content_type:
                args['x-www-form-urlencoded'] = self.desensitization(serialized_form)
            else:
                args['form-data'] = self.desensitization(serialized_form)

        if args:
            args = self.truncate(args)

        return args or None

    @staticmethod
    def truncate(args: dict[str, Any]) -> dict[str, Any]:
        """
        截断处理

        :param args: 需要截断的请求参数字典
        :return:
        """
        max_size = 10240  # 数据最大大小（字节）

        try:
            args_str = json.dumps(args, ensure_ascii=False)
            args_size = len(args_str.encode('utf-8'))

            if args_size > max_size:
                truncated_str = args_str[:max_size]
                return {
                    '_truncated': True,
                    '_original_size': args_size,
                    '_max_size': max_size,
                    '_message': f'数据过大已截断：原始大小 {args_size} 字节，限制 {max_size} 字节',
                    'data_preview': truncated_str,
                }
        except Exception as e:
            log.error(f'请求参数截断处理失败：{e}')

        return args

    @staticmethod
    def desensitization(args: dict[str, Any]) -> dict[str, Any]:
        """
        脱敏处理

        :param args: 需要脱敏的参数字典
        :return:
        """
        for key in args:
            if key in settings.OPERA_LOG_REDACT_KEYS:
                args[key] = '[REDACTED]'
        return args

    @classmethod
    async def consumer(cls) -> None:
        """操作日志消费者"""
        while True:
            logs = await batch_dequeue(
                cls.opera_log_queue,
                max_items=settings.OPERA_LOG_QUEUE_BATCH_CONSUME_SIZE,
                timeout=settings.OPERA_LOG_QUEUE_TIMEOUT,
            )
            if logs:
                try:
                    if settings.DATABASE_ECHO:
                        log.info('自动执行【操作日志批量创建】任务...')
                    async with async_db_session.begin() as db:
                        await opera_log_service.bulk_create(db=db, objs=logs)
                except Exception as e:
                    log.error(f'操作日志入库失败，丢失 {len(logs)} 条日志: {e}')
                finally:
                    for _ in range(len(logs)):
                        cls.opera_log_queue.task_done()
