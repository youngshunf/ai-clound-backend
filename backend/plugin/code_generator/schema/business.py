import re

from datetime import datetime

from pydantic import ConfigDict, Field, field_validator

from backend.common.exception import errors
from backend.common.schema import SchemaBase

_CODEGEN_IDENTIFIER_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')


class GenBusinessSchemaBase(SchemaBase):
    """代码生成业务基础模型"""

    app_name: str = Field(description='应用名称（英文）')
    table_name: str = Field(description='表名称（英文）')
    doc_comment: str = Field(description='文档注释（用于函数/参数文档）')
    table_comment: str | None = Field(None, description='表描述')
    class_name: str | None = Field(None, description='用于 python 代码基础类名')
    schema_name: str | None = Field(None, description='用于 python Schema 代码基础类名')
    filename: str | None = Field(None, description='用于 python 代码基础文件名')
    datetime_mixin: bool = Field(True, description='是否包含时间 Mixin 列')
    api_version: str = Field('v1', description='API 版本')
    tag: str | None = Field(None, description='API 标签（用于路由分组）')
    api_scope: str = Field('admin', description='API scope (admin/app/agent/open，逗号分隔)')
    gen_path: str | None = Field(None, description='生成路径（默认在 backend/app 目录下）')
    remark: str | None = Field(None, description='备注')

    @field_validator('app_name', 'table_name')
    @classmethod
    def validate_codegen_identifier(cls, v: str) -> str:
        """验证可用于代码生成的英文标识符。"""
        if not _CODEGEN_IDENTIFIER_PATTERN.fullmatch(v):
            raise errors.RequestError(msg='必须以英文字母开头，后续只能包含英文字母、数字和下划线')
        return v

    @field_validator('api_scope')
    @classmethod
    def validate_api_scope(cls, v: str) -> str:
        """验证 API scope 值"""
        valid_scopes = {'admin', 'app', 'agent', 'open'}
        scopes = [s.strip() for s in v.split(',')]
        for scope in scopes:
            if scope not in valid_scopes:
                raise errors.RequestError(msg=f'无效的 API scope: {scope}，可选值: {", ".join(valid_scopes)}')
        return ','.join(scopes)


class CreateGenBusinessParam(GenBusinessSchemaBase):
    """创建代码生成业务参数"""


class UpdateGenBusinessParam(GenBusinessSchemaBase):
    """更新代码生成业务参数"""


class GetGenBusinessDetail(GenBusinessSchemaBase):
    """获取代码生成业务详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description='主键 ID')
    created_time: datetime = Field(description='创建时间')
    updated_time: datetime | None = Field(None, description='更新时间')
