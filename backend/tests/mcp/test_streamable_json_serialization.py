"""云端 MCP 工具结果序列化边界（streamable.py）回归测试。

背景：知识库 list_documents/list_folders 等工具的 handler 会直接透传 ORM 行字段
（created_time/updated_time 是 datetime 对象）。streamable.py 的 json.dumps 早期无
default 编码器，遇到 datetime 直接抛 ``Object of type datetime is not JSON serializable``，
让整次 agent 工具调用炸掉（owner HTTP 路径有 FastAPI jsonable_encoder 不受影响，
仅 agent MCP 路径炸）。这里钉死序列化边界对 datetime/date/Decimal 的兜底行为。
"""

import datetime as dt
import json

from decimal import Decimal

from backend.app.mcp.json_encoding import json_default


def test_json_default_datetime_to_isoformat() -> None:
    value = dt.datetime(2026, 6, 26, 8, 10, 0, tzinfo=dt.timezone.utc)
    assert json_default(value) == value.isoformat()


def test_json_default_date_to_isoformat() -> None:
    value = dt.date(2026, 6, 26)
    assert json_default(value) == '2026-06-26'


def test_json_default_decimal_to_float() -> None:
    result = json_default(Decimal('1.5'))
    assert isinstance(result, float)
    assert abs(result - 1.5) < 1e-9


def test_dumps_tool_result_with_nested_datetime_does_not_crash() -> None:
    # 复刻知识库 list_documents 返回形态：信封里嵌套含 datetime 的行
    result = {
        'trace_id': 'trace_x',
        'result': {
            'documents': [
                {
                    'id': 1,
                    'name': 'a.md',
                    'created_time': dt.datetime(2026, 6, 26, 8, 10, 0, tzinfo=dt.timezone.utc),
                    'updated_time': dt.datetime(2026, 6, 26, 9, 0, 0, tzinfo=dt.timezone.utc),
                }
            ]
        },
    }
    text = json.dumps(result, ensure_ascii=False, default=json_default)
    parsed = json.loads(text)
    assert parsed['result']['documents'][0]['created_time'] == '2026-06-26T08:10:00+00:00'


def test_dumps_without_default_would_crash() -> None:
    # 反证：不带 default 时确实会炸（钉死这是真 bug，不是误判）
    payload = {'t': dt.datetime(2026, 6, 26, tzinfo=dt.timezone.utc)}
    try:
        json.dumps(payload)
    except TypeError as exc:
        assert 'not JSON serializable' in str(exc)
    else:  # pragma: no cover - 不应到达
        raise AssertionError('期望原生 json.dumps 对 datetime 抛 TypeError')
