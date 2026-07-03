from pydantic import Field

from backend.common.schema import SchemaBase
from backend.plugin.code_generator.config_loader import codegen_config


class ImportParam(SchemaBase):
    """导入参数"""

    app: str = Field(description='应用名称，用于代码生成到指定 app')
    table_schema: str = Field(description='数据库名')
    table_name: str = Field(description='数据库表名')
    api_scope: str = Field(default_factory=lambda: getattr(codegen_config, 'default_api_scope', 'admin'), description='API scope (admin/app/agent/open，逗号分隔)')
