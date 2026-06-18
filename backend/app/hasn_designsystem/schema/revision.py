from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

# 注：codegen 元数据按裸表名缓存，`revision` 与 hasn_deck.revision 撞名导致首版 schema 复用了
# deck 的字段。本文件已按 backend/sql/hasn_designsystem/revision.sql（权威 DDL）手工校正。


class RevisionSchemaBase(SchemaBase):
    """设计系统版本基础模型"""

    design_system_id: int = Field(description='所属 design_system.id')
    rev_no: int = Field(description='版本号（design_system 内单调递增，从 1 起）')
    author_kind: str = Field('human', description='作者类型 (human:人/agent:分身)')
    author_id: str = Field('', description='作者 HASN ID（人或分身）')
    bundle_asset_id: str | None = Field(None, description='完整 bundle zip 资产引用（hasn://asset/{id}）')
    tokens_css: str | None = Field(None, description='真源 tokens.css（四层 token 契约）')
    design_tokens_json: dict | None = Field(None, description='派生 design-tokens.json（含分层/血缘/评分）')
    tailwind_css: str | None = Field(None, description='派生 tailwind-v4.css（@theme 映射）')
    design_md: str | None = Field(None, description='设计说明 DESIGN.md（创意部分，分身产出）')
    components_html: str | None = Field(None, description='组件样例 components.html')
    components_manifest_json: dict | None = Field(None, description='组件清单 components.manifest.json')
    token_contract_report_json: dict | None = Field(None, description='token 契约评分 + 血缘报告')
    note: str | None = Field(None, description='版本备注（如"主色调暖一点"）')


class CreateRevisionParam(RevisionSchemaBase):
    """创建设计系统版本参数"""


class UpdateRevisionParam(RevisionSchemaBase):
    """更新设计系统版本参数"""


class DeleteRevisionParam(SchemaBase):
    """删除设计系统版本参数"""

    pks: list[int] = Field(description='设计系统版本 ID 列表')


class GetRevisionDetail(RevisionSchemaBase):
    """设计系统版本详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
