import inspect
import logging
import os
import re
import sys
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from backend.core.conf import settings
from backend.core.path_conf import LOG_DIR
from backend.utils.timezone import timezone
from backend.utils.trace_id import get_request_trace_id

if TYPE_CHECKING:
    from loguru import Record


class InterceptHandler(logging.Handler):
    """
    日志拦截处理器，用于将标准库的日志重定向到 loguru

    参考：https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    """

    def emit(self, record: logging.LogRecord) -> None:
        # 获取对应的 Loguru 级别（如果存在）
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 查找记录日志消息的调用者
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def default_formatter(record: 'Record') -> str:
    """默认日志格式化程序"""

    # 重写 sqlalchemy echo 输出
    # https://github.com/sqlalchemy/sqlalchemy/discussions/12791
    record_name = record['name'] or ''
    if record_name.startswith('sqlalchemy'):
        record['message'] = re.sub(r'\s+', ' ', record['message']).strip()

    return settings.LOG_FORMAT if settings.LOG_FORMAT.endswith('\n') else f'{settings.LOG_FORMAT}\n'


def request_id_filter(record: 'Record') -> bool:
    """请求 ID 过滤器"""
    rid = get_request_trace_id()
    # LOG_FORMAT 沿用 `{request_id}` 顶层占位符；Loguru 运行时记录是可扩展字典，类型桩未列出该自定义键。
    record_data = cast(dict[str, Any], record)
    record_data['request_id'] = rid[: settings.TRACE_ID_LOG_LENGTH]
    return True


def _standard_output_filter(record: 'Record') -> bool:
    """标准输出保留非 LLM 调试日志，并注入请求追踪 ID。"""
    return request_id_filter(record) and not record.get('extra', {}).get('llm_debug', False)


def _access_log_filter(record: 'Record') -> bool:
    """访问日志文件仅接收 INFO 及以下的非 LLM 调试日志。"""
    return request_id_filter(record) and record['level'].no <= 25 and not record.get('extra', {}).get('llm_debug', False)


def _error_log_filter(record: 'Record') -> bool:
    """错误日志文件仅接收 WARNING 以上的非 LLM 调试日志。"""
    return request_id_filter(record) and record['level'].no >= 30 and not record.get('extra', {}).get('llm_debug', False)


def setup_logging() -> None:
    """
    设置日志处理器

    参考：
    - https://github.com/benoitc/gunicorn/issues/1572#issuecomment-638391953
    - https://github.com/pawamoy/pawamoy.github.io/issues/17
    """
    # 设置根日志处理器和级别
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(settings.LOG_STD_LEVEL)

    for name in logging.root.manager.loggerDict.keys():
        # 清空所有默认日志处理器
        logging.getLogger(name).handlers = []

        # 配置日志传播规则
        if 'uvicorn.access' in name or 'watchfiles.main' in name:
            logging.getLogger(name).propagate = False
        else:
            logging.getLogger(name).propagate = True

        # Debug log handlers
        # logging.debug(f'{logging.getLogger(name)}, {logging.getLogger(name).propagate}')

    # 移除 loguru 默认处理器
    logger.remove()

    # 配置 loguru 处理器
    logger.add(
        sys.stdout,
        level=settings.LOG_STD_LEVEL,
        format=default_formatter,
        filter=_standard_output_filter,
    )


def set_custom_logfile() -> None:
    """设置自定义日志文件"""
    if not os.path.exists(LOG_DIR):
        os.mkdir(LOG_DIR)

    # 日志文件
    log_access_file = LOG_DIR / settings.LOG_ACCESS_FILENAME
    log_error_file = LOG_DIR / settings.LOG_ERROR_FILENAME

    # 日志压缩回调
    def compression(filepath: str) -> None:
        filename = filepath.split(os.sep)[-1]
        original_filename = filename.split('.')[0]
        if '-' in original_filename:
            destination = LOG_DIR / f'{original_filename}.log'
        else:
            destination = LOG_DIR / f'{original_filename}_{timezone.now().strftime("%Y-%m-%d")}.log'
        os.rename(filepath, destination)

    # 标准输出文件
    logger.add(
        str(log_access_file),
        level=settings.LOG_FILE_ACCESS_LEVEL,
        filter=_access_log_filter,
        backtrace=False,
        diagnose=False,
        format=default_formatter,
        enqueue=True,
        rotation='00:00',
        retention='7 days',
        compression=compression,
    )

    # 标准错误文件
    logger.add(
        str(log_error_file),
        level=settings.LOG_FILE_ERROR_LEVEL,
        filter=_error_log_filter,
        backtrace=True,
        diagnose=True,
        format=default_formatter,
        enqueue=True,
        rotation='00:00',
        retention='7 days',
        compression=compression,
    )


# 创建 logger 实例
log = logger
