"""云端 MCP 工具结果 JSON 序列化兜底编码器。

独立成模块（不导入 `mcp` SDK）：streamable.py 因 `from mcp import types` 在 pytest 下
会被本地 `backend.app.mcp` / `backend.tests.mcp` 包遮蔽 site-packages 的 mcp，整文件无法
在单测中导入。把纯函数编码器拆出来，既给 streamable 复用，也能被单测直接导入校验。
"""

import datetime as dt

from decimal import Decimal
from typing import Any


def json_default(value: Any) -> Any:
    """工具结果 json.dumps 的兜底编码器（云端所有 MCP 工具结果的唯一序列化边界）。

    工具 handler 返回的 dict 可能直接透传 ORM 行字段（如知识库 list_documents/list_folders
    的 created_time/updated_time 是 datetime 对象），而原生 json.dumps 不识别 datetime/Decimal，
    会抛 ``Object of type datetime is not JSON serializable`` 让整次工具调用炸掉。
    owner HTTP 路径靠 FastAPI jsonable_encoder 自动转 ISO；agent MCP 路径走这里的 json.dumps，
    必须在此对齐：datetime/date → isoformat（与 owner 路径一致），Decimal → float，
    其余不可序列化类型兜底 str（序列化边界绝不允许崩）。
    """
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)
