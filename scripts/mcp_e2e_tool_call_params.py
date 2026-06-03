# ruff: noqa: E501
"""端到端复现 + 验证「tool.call 参数透传」线上 bug 修复。

线上现象：Agent 经云端 `hasn.cloud.tool.call` 调内层工具时，params 落成空对象 {}，
内层工具一直报 missing query。根因：params 在 input_schema 里是裸 object（无字段、
无 additionalProperties），function-calling Runtime/LLM 把它当「不接受任何字段」，
只能产出 params={}。

修复：服务端宽容接收三种到达形态，并把 params 声明成开放对象。本脚本对真实 :8020
（reload 已加载新代码）用 raw MCP 客户端模拟三种 Runtime 行为：
  S1 嵌套对象 params={query,limit}（基线）
  S2 JSON 字符串 params='{"query":...}'（部分 Runtime 序列化）
  S3 平铺顶层 {name, query, limit}（部分 Runtime 不嵌套）
三种都应让内层 hasn.community.search 真正收到 query（不再 missing query）。
"""

import asyncio
import json
import os

from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import text

from backend.app.hasn.schema.hasn_agent_mcp_keys import IssueAgentMcpKeyParam
from backend.app.hasn.service.hasn_agent_mcp_keys_service import hasn_agent_mcp_keys_service
from backend.common.security.agent_jwt import update_agent_modes
from backend.database.db import async_db_session

MCP_PORT = os.environ.get('MCP_PORT', '8020')
MCP_URL = f'http://127.0.0.1:{MCP_PORT}/api/v1/mcp/streamable'
AGENT = 'a_3dbae149-919e-4ab5-956e-c5147d4f1ac9'  # 安然
OWNER_HASN = 'h_47094e96-ead5-4180-959a-8a28fac942e6'
SEARCH = 'hasn.community.search'
TOOL_CALL = 'hasn.cloud.tool.call'


def _unwrap(result: Any) -> dict[str, Any]:
    is_error = getattr(result, 'isError', False)
    parts = []
    for item in getattr(result, 'content', []) or []:
        txt = getattr(item, 'text', None)
        if txt is None:
            continue
        try:
            parts.append(json.loads(txt))
        except (ValueError, TypeError):
            parts.append(txt)
    return {'is_error': is_error, 'payload': parts[0] if len(parts) == 1 else parts}


def _received_query(res: dict[str, Any]) -> bool:
    """判定内层 search 是否真正收到 query：未报 missing query / input_validation_failed，
    且不是因缺 query 失败。收到 query 时应返回正常 search 结果信封（或业务结果）。"""
    if res.get('is_error'):
        return False
    payload = res.get('payload')
    if not isinstance(payload, dict):
        return True  # 非 dict 文本结果也算到达内层
    # schema-on-error：缺 query 会回吐 input_validation_failed + missing 含 query
    if payload.get('error') == 'input_validation_failed':
        return 'query' not in (payload.get('missing') or [])
    # 内层业务层若仍因空 query 报错
    blob = json.dumps(payload, ensure_ascii=False).lower()
    if 'missing query' in blob or "'query'" in blob and 'required' in blob:
        return False
    return True


async def main() -> dict[str, Any]:
    report: dict[str, Any] = {}
    async with async_db_session() as db:
        owner_user_id = (
            await db.execute(text('select user_id from hasn_humans where hasn_id=:h'), {'h': OWNER_HASN})
        ).scalar_one()
        issued = await hasn_agent_mcp_keys_service.issue(
            db,
            obj=IssueAgentMcpKeyParam(agent_hasn_id=AGENT, scopes=[], node_id=None, expire_time=None),
            owner_hasn_id=OWNER_HASN,
            owner_user_id=int(owner_user_id),
        )
        await db.commit()
        key, pk = issued.key, issued.id
    print(f'[mint] key id={pk}  url={MCP_URL}')

    try:
        async with async_db_session() as db:
            await update_agent_modes(db, AGENT, default_mode='allow', capability_modes={})
            await db.commit()

        headers = {'Authorization': f'Bearer {key}'}
        async with streamablehttp_client(MCP_URL, headers=headers) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()

                # 基线对照：tool.call 只带 name、不带任何 params（线上等价的「丢参」形态）→ 应报 missing query
                res0 = _unwrap(await session.call_tool(TOOL_CALL, {'name': SEARCH}))
                report['S0_no_params'] = res0
                s0_missing = not _received_query(res0)
                print(f'[S0] tool.call(search) 无 params → 内层报缺 query? {"✅(符合预期)" if s0_missing else "❌"}')

                # S1 嵌套对象
                res1 = _unwrap(await session.call_tool(TOOL_CALL, {'name': SEARCH, 'params': {'query': 'hasn-node架构总览', 'limit': 10}}))
                report['S1_nested_object'] = res1
                s1 = _received_query(res1)
                print(f'[S1] params=对象 → 内层收到 query? {"✅" if s1 else "❌"}')

                # S2 JSON 字符串
                res2 = _unwrap(await session.call_tool(TOOL_CALL, {'name': SEARCH, 'params': json.dumps({'query': 'hasn-node架构总览', 'limit': 10})}))
                report['S2_json_string'] = res2
                s2 = _received_query(res2)
                print(f'[S2] params=JSON字符串 → 内层收到 query? {"✅" if s2 else "❌"}')

                # S3 平铺顶层
                res3 = _unwrap(await session.call_tool(TOOL_CALL, {'name': SEARCH, 'query': 'hasn-node架构总览', 'limit': 10}))
                report['S3_flattened'] = res3
                s3 = _received_query(res3)
                print(f'[S3] 参数平铺顶层 → 内层收到 query? {"✅" if s3 else "❌"}')

                report['summary_ok'] = bool(s0_missing and s1 and s2 and s3)
                print(f'\n=== summary_ok={report["summary_ok"]} （S0 对照缺参 + S1/S2/S3 三形态均透传）===')
    finally:
        async with async_db_session() as db:
            await update_agent_modes(db, AGENT, default_mode='allow', capability_modes={})
            await db.commit()
            await hasn_agent_mcp_keys_service.revoke(db, pk=pk, owner_hasn_id=OWNER_HASN)
            await db.commit()
        print(f'[cleanup] 已吊销 key id={pk} + 恢复三态默认 allow')

    return report


if __name__ == '__main__':
    final_report = asyncio.run(main())
    os.makedirs('test-results', exist_ok=True)
    with open('test-results/mcp_e2e_tool_call_params.json', 'w', encoding='utf-8') as fp:
        json.dump(final_report, fp, ensure_ascii=False, indent=2)
    print('\n=== 写入 test-results/mcp_e2e_tool_call_params.json ===')
