"""端到端验证：真实 Agent 通过云端 MCP 操作能力市场（15-技能市场/11-doc）。

真实链路（安然，owner h_47094e96…）：mint key scopes=[]（证明授权不依赖 key 快照，D3）。
  L1: tools/list 含 marketplace.* 工具（搜/看/装/卸/发）。
  L2: search_skills 返回 published 技能；get_skill 真实存在=found、不存在=found:false（维度②）。
  L3: install_skill 不存在技能 → reachable:false（维度②不静默成功）；真实技能 → installed:true。
  L4: list_installed 含刚装技能；uninstall_skill → uninstalled:true（清理）。
  L5: marketplace:install=deny（三态活取）→ install_skill 从 tools 消失 + 调用被拒；恢复。

结束：卸载测试技能、吊销临时 key、恢复三态默认 allow（零残留）。
MCP 端口可配：MCP_PORT=8030 python scripts/mcp_e2e_marketplace.py（默认 8020）。
"""
# ruff: noqa: E501  —— E2E 脚本：深层嵌套的断言/打印行允许超长，便于逐级对照阅读。

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
AGENT = 'a_3dbae149-919e-4ab5-956e-c5147d4f1ac9'  # 安然 (health)
OWNER_HASN = 'h_47094e96-ead5-4180-959a-8a28fac942e6'
TEST_SKILL = 'github/anthropics-skills/xlsx'  # 真实 published/public 技能
MISSING_SKILL = 'user/h_nope/does-not-exist-xyz'


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


async def _set_mode(default_mode: str, capability_modes: dict) -> None:
    async with async_db_session() as db:
        await update_agent_modes(db, AGENT, default_mode=default_mode, capability_modes=capability_modes)
        await db.commit()


def _mark(ok: bool) -> str:  # noqa: FBT001
    return '✅' if ok else '❌'


async def main() -> dict[str, Any]:
    report: dict[str, Any] = {'mcp_url': MCP_URL}
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
    print(f'[mint] key id={pk}  (scopes=[] —— 证明授权不依赖 key 快照)')

    try:
        await _set_mode('allow', {})  # 基线全开
        # 预清理：确保测试技能不在安然身上，让 install 真实从无到有。
        await _uninstall_quietly()

        headers = {'Authorization': f'Bearer {key}'}
        async with streamablehttp_client(MCP_URL, headers=headers) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()

                # L1：渐进式披露——tools/list 只回 bootstrap；长尾经 hasn.cloud.tool.search 发现。
                bootstrap = [t.name for t in (await session.list_tools()).tools]
                report['L1_bootstrap'] = bootstrap
                disc = _unwrap(await session.call_tool('hasn.cloud.tool.search', {'query': 'marketplace'}))
                disc_payload = disc.get('payload') or {}
                mk = [t.get('name') for t in (disc_payload.get('tools') or []) if str(t.get('name', '')).startswith('hasn.marketplace.')]
                report['L1_discovered'] = mk
                expected = {
                    'hasn.marketplace.search_skills', 'hasn.marketplace.get_skill',
                    'hasn.marketplace.search_templates', 'hasn.marketplace.get_template',
                    'hasn.marketplace.list_installed', 'hasn.marketplace.install_skill',
                    'hasn.marketplace.uninstall_skill', 'hasn.marketplace.publish_skill',
                    'hasn.marketplace.publish_template',
                }
                l1_ok = expected.issubset(set(mk))
                print(f'\n[L1] bootstrap={bootstrap}')
                print(f'[L1] tool.search("marketplace") 发现 {len(mk)} 个; 9 工具齐全? {_mark(l1_ok)}')

                # L2：search_skills + get_skill（真实/不存在）
                res_search = _unwrap(await session.call_tool('hasn.marketplace.search_skills', {'limit': 5}))
                search_payload = res_search.get('payload') or {}
                n_results = len(search_payload.get('results', [])) if isinstance(search_payload, dict) else 0
                res_get = _unwrap(await session.call_tool('hasn.marketplace.get_skill', {'skill_id': TEST_SKILL}))
                get_ok = isinstance(res_get.get('payload'), dict) and res_get['payload'].get('found') is True
                res_get_miss = _unwrap(await session.call_tool('hasn.marketplace.get_skill', {'skill_id': MISSING_SKILL}))
                get_miss_ok = isinstance(res_get_miss.get('payload'), dict) and res_get_miss['payload'].get('found') is False
                report['L2_search_count'] = n_results
                report['L2_get_real'] = res_get.get('payload')
                report['L2_get_missing'] = res_get_miss.get('payload')
                print(f'\n[L2] search_skills 返回 {n_results} 条; get_skill 真实={_mark(get_ok)} 不存在found:false={_mark(get_miss_ok)}')

                # L3：install 不存在（维度②）+ 真实安装
                res_inst_miss = _unwrap(await session.call_tool('hasn.marketplace.install_skill', {'skill_id': MISSING_SKILL}))
                miss_payload = res_inst_miss.get('payload') or {}
                inst_miss_ok = isinstance(miss_payload, dict) and miss_payload.get('reachable') is False and miss_payload.get('installed') is False
                res_inst = _unwrap(await session.call_tool('hasn.marketplace.install_skill', {'skill_id': TEST_SKILL}))
                inst_payload = res_inst.get('payload') or {}
                inst_ok = isinstance(inst_payload, dict) and inst_payload.get('installed') is True and inst_payload.get('reachable') is True
                report['L3_install_missing'] = miss_payload
                report['L3_install_real'] = inst_payload
                print(f'\n[L3] install 不存在→reachable:false={_mark(inst_miss_ok)}; 真实install→installed:true={_mark(inst_ok)} rev={inst_payload.get("revision")}')

                # L4：list_installed 含刚装；uninstall 清理
                res_list = _unwrap(await session.call_tool('hasn.marketplace.list_installed', {}))
                list_payload = res_list.get('payload') or {}
                installed_ids = [s.get('skill_id') for s in (list_payload.get('installed') or [])]
                list_ok = TEST_SKILL in installed_ids
                res_uninst = _unwrap(await session.call_tool('hasn.marketplace.uninstall_skill', {'skill_id': TEST_SKILL}))
                uninst_payload = res_uninst.get('payload') or {}
                uninst_ok = isinstance(uninst_payload, dict) and uninst_payload.get('uninstalled') is True
                report['L4_installed_ids'] = installed_ids
                report['L4_uninstall'] = uninst_payload
                print(f'\n[L4] list_installed 含刚装技能={_mark(list_ok)}; uninstall→uninstalled:true={_mark(uninst_ok)}')

                # L5：marketplace:install=deny → 发现层隐藏 install_skill + 调用被拒（三态活取）
                await _set_mode('allow', {'marketplace:install': 'deny'})
                disc_deny = _unwrap(await session.call_tool('hasn.cloud.tool.search', {'query': 'marketplace'}))
                deny_disc_names = [t.get('name') for t in ((disc_deny.get('payload') or {}).get('tools') or [])]
                gone = 'hasn.marketplace.install_skill' not in deny_disc_names
                res_deny = _unwrap(await session.call_tool('hasn.marketplace.install_skill', {'skill_id': TEST_SKILL}))
                deny_payload = res_deny.get('payload')
                denied = (isinstance(deny_payload, dict) and deny_payload.get('decision') == 'deny') or res_deny.get('is_error')
                report['L5_deny_gone'] = gone
                report['L5_deny_call'] = deny_payload
                print(f'\n[L5] install=deny → 工具消失={_mark(gone)}; 调用被拒={_mark(bool(denied))}')

        report['summary_ok'] = bool(l1_ok and get_ok and get_miss_ok and inst_miss_ok and inst_ok and list_ok and uninst_ok and gone and denied)
    finally:
        await _set_mode('allow', {})
        await _uninstall_quietly()
        async with async_db_session() as db:
            await hasn_agent_mcp_keys_service.revoke(db, pk=pk, owner_hasn_id=OWNER_HASN)
            await db.commit()
        print(f'\n[cleanup] 恢复三态 allow + 卸载测试技能 + 吊销 key id={pk}')

    return report


async def _uninstall_quietly() -> None:
    """直调 service 把测试技能从安然卸掉（幂等，确保 install 真实从无到有 + 收尾零残留）。"""
    from backend.app.hasn.service.hasn_agents_service import agent_profile_service

    async with async_db_session() as db:
        owner_user_id = (
            await db.execute(text('select user_id from hasn_humans where hasn_id=:h'), {'h': OWNER_HASN})
        ).scalar_one()
        try:
            await agent_profile_service.detach_skill_cloud_first(
                db, owner_id=OWNER_HASN, hasn_id=AGENT, skill_id=TEST_SKILL, user_id=int(owner_user_id)
            )
            await db.commit()
        except Exception:
            await db.rollback()


if __name__ == '__main__':
    final_report = asyncio.run(main())
    os.makedirs('test-results', exist_ok=True)
    with open('test-results/mcp_e2e_marketplace.json', 'w', encoding='utf-8') as fp:
        json.dump(final_report, fp, ensure_ascii=False, indent=2)
    print('\n[done] summary_ok =', final_report.get('summary_ok'))
