"""平台工具 · artifact 域（产物化 P6，分身显式登记一条产物）。

把分身的 `hasn.artifact.record` 从 hasn-node 本地 hasn-mcp 迁到**云端 platform MCP 工具**
（不操作本地文件/数据 → 走云端，与 contact/message/plan/notification 同范式）：分身经
`/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `hasn_artifacts_service.record`
（in-process，**不再**经 daemon → `/api/v1/artifacts/agent/record` HTTP relay）。

与 hasn-mcp `audited` 的 best-effort 自动捕获（其它工具产物的副作用，仍留本地）不同，这是分身
**主动**把一份成果落库——尤其用于**文本/markdown 产物直接入库**（`kind=document, body=<markdown>`，
不转文件上传）。本体三选一（互斥）：`body` / `asset_id` / `resource_uri`。带 `origin_ref`
（如 `resource:plan:todo:{id}`）则可被该业务对象详情页的产物轨按 origin 反查（P6 §6.5）。

身份恒由 `agent_context`（取自 Agent JWT/MCP Key）注入，body 绝不含 agent/owner；云端按白名单
归一 kind、按 `(agent, dispatch_id, asset_id)` 去重幂等。

- 工具名 + input_schema 与原 hasn-mcp `ArtifactRecordTool` **逐字段 1:1**。
- 与本地工具一致：**不声明 capability scope**（低风险账本写、身份钉死本主人）；三态闸门由
  `server.call_tool` 统一判定（出厂 Allow），工具体不二次校验。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

_ARTIFACT_KINDS = ['image', 'voice', 'video', 'file', 'document', 'deck', 'webpage', 'dataset', 'other']
_SOURCE_KINDS = ['task_result', 'tool_output', 'upload', 'external']


class ArtifactRecordTool(BaseTool):
    """`hasn.artifact.record`：分身显式登记一条产物到统一产物表（cloud-hosted，P6）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.artifact.record'

    @property
    def namespace(self) -> str:
        return 'hasn.artifact'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '把一份成果显式登记到统一产物表（产物化 P6）：文本/markdown 用 kind=document + body 直接入库'
            '（不上传文件），二进制用 asset_id，资源用 resource_uri（三选一）。带 origin_ref'
            '（如 resource:plan:todo:{id}）则该业务对象详情页的产物轨可按 origin 反查。'
            '身份恒为本 Agent（云端按凭证注入），归本人主人所有。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'kind': {
                    'type': 'string',
                    'enum': _ARTIFACT_KINDS,
                    'description': '产物类型（见 enum）；文本/markdown 用 document',
                },
                'title': {'type': 'string', 'description': '展示标题（可选，<=200 字）'},
                'summary': {'type': 'string', 'description': '简要描述（可选）'},
                'body': {
                    'type': 'string',
                    'description': (
                        '文本/markdown 正文（document 文本产物用，直接入库不上传文件；'
                        '与 asset_id/resource_uri 三选一）'
                    ),
                },
                'asset_id': {'type': 'string', 'description': '已上传资产 ID（image/voice/file 二进制产物用）'},
                'resource_uri': {'type': 'string', 'description': 'hasn:// 资源 URI（deck/webpage 等本体即资源时用）'},
                'origin_ref': {
                    'type': 'string',
                    'description': '产出所属业务资源（如 resource:plan:todo:{id}），供该对象详情产物轨反查',
                },
                'source_kind': {
                    'type': 'string',
                    'enum': _SOURCE_KINDS,
                    'description': '产出来源（默认 task_result）',
                },
            },
            'required': ['kind'],
        }

    @property
    def required_scopes(self) -> list[str]:
        # 与本地 hasn-mcp 工具 1:1：不声明独立 capability scope（低风险账本写、身份钉死本主人）。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """整形入参 → 云端 hasn_artifacts_service.record（in-process，自动提交）。

        维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验。
        """

        def _str(key: str) -> str | None:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        kind = _str('kind')
        if not kind:
            raise RuntimeError("artifact.record: 'kind' 必填")

        body = _str('body')
        asset_id = _str('asset_id')
        resource_uri = _str('resource_uri')
        # 本体三选一（互斥）：文本 body / 二进制 asset_id / 资源 resource_uri，至少其一。
        if not body and not asset_id and not resource_uri:
            raise RuntimeError(
                'artifact.record: 必须带 body、asset_id 或 resource_uri 其一（文本走 body 不上传文件）'
            )

        params = RecordArtifactParam(
            kind=kind,
            title=_str('title'),
            summary=_str('summary'),
            body=body,
            asset_id=asset_id,
            resource_uri=resource_uri,
            origin_ref=_str('origin_ref'),
            # 显式登记默认归「任务成果」（区别于工具副作用 tool_output）。
            source_kind=_str('source_kind') or 'task_result',
            source_tool='hasn.artifact.record',
        )

        # 写类 → .begin() 自动提交（service 只 flush 不 commit）。
        async with async_db_session.begin() as db:
            artifact_id = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent_context.agent_hasn_id,
                owner_hasn_id=agent_context.owner_hasn_id,
                params=params,
            )
        return {'artifact_id': artifact_id}


ARTIFACT_TOOLS: list[BaseTool] = [ArtifactRecordTool()]
