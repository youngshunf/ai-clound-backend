"""平台工具 · owner 域（主人画像完整度，「了解主人」模块 19）。

`hasn.owner.coverage.get`：采访分身（规划参谋）开工先读「主人 5 个画像维度还缺哪几维」，
据此定向采访（缺什么采访什么）——不一次性甩问卷。纯云端只读：直调云端权威
`OwnerProfileCoverageService.assess_if_stale`（owner_memory 版本领先时惰性重判，否则快读），
所以采访分身 contribute→合并（owner_memory.version+1）后再调本工具，即拿到重判后的最新缺口
（写入→合并→重判闭环落在分身定向采访前这一点，无需把 LLM 打分塞进 contribute 热路径）。

owner 身份由 Agent JWT/MCP Key 解析出的 `agent_context.owner_hasn_id` 强制，绝不入请求体
（CLAUDE.md hasn-mcp/BackendGateway 统一调用 + owner 隔离硬约束）。读类无 scope（只读自己主人数据）。

设计事实源：docs/hasn-node设计文档/19-规划与目标管理/03-了解主人：采访建档·完整度判定·主动规划闭环设计.md §5.3 / §6.1。
"""

from __future__ import annotations

from typing import Any

from backend.app.hasn_memory.service.owner_profile_coverage_service import owner_profile_coverage_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

NAMESPACE = 'hasn.owner'


class OwnerCoverageGetTool(BaseTool):
    """读主人 5 维画像完整度（缺什么采访什么）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.owner.coverage.get'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '读取主人 5 个画像维度（兴趣爱好 interests / 工作情况 work / 居住地址 residence / '
            '近期目标 goals / 人生规划 life_plan）的了解完整度，用于采访建档时定向采访「还缺哪几维」。'
            '返回每维度 status（missing 完全不知 / partial 知道一点 / sufficient 已足够）+ 已知摘要 summary + '
            '待补提示 missing_hint，以及 all_sufficient（5 维全 sufficient 则无需再采访）、'
            'sufficient_count、next_dimensions（建议优先采访的非充分维度）、memory_version。'
            '只读主人自己的数据，无需授权。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}, 'additionalProperties': False}

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        # 只读：用普通 session（assess_if_stale 内部按需重判并自提交，独立 session 安全，
        # 与 owner 读 API 同范式）。owner 身份强制取自 agent_context，绝不读 arguments。
        async with async_db_session() as db:
            return await owner_profile_coverage_service.assess_if_stale(
                db, owner_id=agent_context.owner_hasn_id
            )


OWNER_TOOLS: list[BaseTool] = [OwnerCoverageGetTool()]
