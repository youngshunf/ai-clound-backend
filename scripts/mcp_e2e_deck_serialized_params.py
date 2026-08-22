# ruff: noqa: E501
"""端到端复现 + 验证「deck 写工具字段值被序列化」线上 bug 修复。

线上现象（分身报障）：经云端 `hasn.cloud.tool.call` 调 `hasn.deck.page.write` /
`page.write_batch` / `outline.set` 一律返回 `input_validation_failed`，而同域的
`create` / `page.edit` 正常。差别在于前三个的**必填字段含非 string 类型**
（`position: integer`、`pages: array`），后两个全是 string。

根因：`_extract_params` 只把**顶层** params 从 JSON 字符串还原成 dict，字段值这一层
没人还原；function-calling Runtime 常把嵌套容器序列化成 JSON 字符串、把数值当字符串填，
撞上 `Draft202012Validator` 的严格类型校验就整调用判死。

修复：转发前按内层 schema 再宽容还原一层（`coerce_params_to_schema`），转不动的原样交给
校验器如实报错。本脚本对真实 :8020 用 raw MCP 客户端跑真链路（不是 service 层）：
  D1 page.write     · deck_id 传整数 + position 字符串化
  D2 page.write_batch · pages 整个是 JSON 字符串，且串内 position 也是字符串（双重序列化）
  D3 outline.set    · pages 是 JSON 字符串
  D4 反向证伪       · position 填纯文字 → 必须**仍然**报 input_validation_failed（零 fake）
判据不是「没报错」，而是**页真的落库了**（回读 deck 详情核对页数与 position）。
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
TOOL_CALL = 'hasn.cloud.tool.call'

# 骨架校验要求主体片段（不带 <html>/<head>/<body>），这里给最小合法页。
PAGE_HTML = '<div class="w-full h-full flex items-center justify-center"><h1>唤星天使轮</h1></div>'


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


def _rejected_for_validation(res: dict[str, Any]) -> bool:
    payload = res.get('payload')
    return isinstance(payload, dict) and payload.get('error') == 'input_validation_failed'


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

    deck_id: str | None = None
    try:
        async with async_db_session() as db:
            await update_agent_modes(db, AGENT, default_mode='allow', capability_modes={})
            await db.commit()

        headers = {'Authorization': f'Bearer {key}'}
        async with streamablehttp_client(MCP_URL, headers=headers) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()

                # 前置：建一个 deck（全 string 入参，本就不受影响）
                created = _unwrap(
                    await session.call_tool(
                        TOOL_CALL,
                        {'name': 'hasn.deck.create', 'params': {'title': 'E2E·字段值序列化回归'}},
                    )
                )
                report['setup_create'] = created
                deck_id = str((created.get('payload') or {}).get('deck_id') or '')
                print(f'[setup] 建 deck → deck_id={deck_id or "❌ 失败"}')
                if not deck_id:
                    report['summary_ok'] = False
                    return report

                # D1：deck_id 传整数 + position 字符串化
                d1 = _unwrap(
                    await session.call_tool(
                        TOOL_CALL,
                        {
                            'name': 'hasn.deck.page.write',
                            'params': {'deck_id': int(deck_id), 'position': '0', 'html': PAGE_HTML, 'title': '封面'},
                        },
                    )
                )
                report['D1_page_write_stringified_int'] = d1
                d1_ok = int((d1.get('payload') or {}).get('written') or 0) == 1
                print(f'[D1] page.write（deck_id=整数, position="0"）→ 落库? {"✅" if d1_ok else "❌"}')

                # D2：pages 整个是 JSON 字符串，且串内 position 也是字符串
                d2 = _unwrap(
                    await session.call_tool(
                        TOOL_CALL,
                        {
                            'name': 'hasn.deck.page.write_batch',
                            'params': {
                                'deck_id': deck_id,
                                'pages': json.dumps(
                                    [
                                        {'position': '1', 'html': PAGE_HTML, 'title': '执行摘要'},
                                        {'position': '2', 'html': PAGE_HTML, 'title': '问题四缺'},
                                    ]
                                ),
                            },
                        },
                    )
                )
                report['D2_write_batch_json_string'] = d2
                d2_ok = int((d2.get('payload') or {}).get('written') or 0) == 2
                print(f'[D2] page.write_batch（pages=JSON串, 串内 position 也是串）→ 落库2页? {"✅" if d2_ok else "❌"}')

                # D3：outline.set 的 pages 是 JSON 字符串
                d3 = _unwrap(
                    await session.call_tool(
                        TOOL_CALL,
                        {
                            'name': 'hasn.deck.outline.set',
                            'params': {
                                'deck_id': deck_id,
                                'pages': json.dumps([{'title': '封面'}, {'title': '执行摘要'}, {'title': '问题四缺'}]),
                            },
                        },
                    )
                )
                report['D3_outline_set_json_string'] = d3
                outline = (((d3.get('payload') or {}).get('deck') or {}).get('outline') or {})
                d3_ok = len(outline.get('items') or []) == 3
                print(f'[D3] outline.set（pages=JSON串）→ 大纲落 3 项? {"✅" if d3_ok else "❌"}')

                # D4 反向证伪：真错型必须仍然被拒（宽容还原不得退化成猜）
                d4 = _unwrap(
                    await session.call_tool(
                        TOOL_CALL,
                        {
                            'name': 'hasn.deck.page.write',
                            'params': {'deck_id': deck_id, 'position': '第一页', 'html': PAGE_HTML},
                        },
                    )
                )
                report['D4_genuine_type_error_still_rejected'] = d4
                d4_ok = _rejected_for_validation(d4)
                print(f'[D4] page.write（position="第一页" 真错型）→ 仍报 input_validation_failed? {"✅(符合预期)" if d4_ok else "❌"}')

                # 回读核对：判据是页真的在库里，不是「工具没报错」
                got = _unwrap(await session.call_tool(TOOL_CALL, {'name': 'hasn.deck.get', 'params': {'deck_id': deck_id}}))
                pages = (got.get('payload') or {}).get('pages') or []
                positions = sorted(int(p['position']) for p in pages)
                report['readback_positions'] = positions
                read_ok = positions == [0, 1, 2]
                print(f'[回读] deck {deck_id} 实际页 position = {positions} → {"✅" if read_ok else "❌ 期望 [0, 1, 2]"}')

                report['summary_ok'] = bool(d1_ok and d2_ok and d3_ok and d4_ok and read_ok)
                print(f'\n=== summary_ok={report["summary_ok"]} （D1/D2/D3 三形态落库 + D4 真错型仍拒 + 回读一致）===')
    finally:
        async with async_db_session() as db:
            if deck_id:
                # 清掉本次 E2E 造的 deck，别把测试数据留给主人的列表。
                await db.execute(text('update hasn_deck.deck set deleted_time=now() where id=:i'), {'i': int(deck_id)})
            await update_agent_modes(db, AGENT, default_mode='allow', capability_modes={})
            await hasn_agent_mcp_keys_service.revoke(db, pk=pk, owner_hasn_id=OWNER_HASN)
            await db.commit()
        print(f'[cleanup] 已吊销 key id={pk} + 软删 E2E deck={deck_id}')

    return report


if __name__ == '__main__':
    final_report = asyncio.run(main())
    os.makedirs('test-results', exist_ok=True)
    with open('test-results/mcp_e2e_deck_serialized_params.json', 'w', encoding='utf-8') as fp:
        json.dump(final_report, fp, ensure_ascii=False, indent=2)
    print('\n=== 写入 test-results/mcp_e2e_deck_serialized_params.json ===')
