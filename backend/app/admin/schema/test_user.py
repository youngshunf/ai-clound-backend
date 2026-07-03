from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class TestUserSchemaBase(SchemaBase):
    """测试用户基础模型"""
    username: str = Field(description='用户名')
    email: str | None = Field(None, description='邮箱地址')
    status: int | None = Field(None, description='状态')
    user_type: int | None = Field(None, description='用户类型')


class CreateTestUserParam(TestUserSchemaBase):
    """创建测试用户参数"""


class UpdateTestUserParam(TestUserSchemaBase):
    """更新测试用户参数"""


class DeleteTestUserParam(SchemaBase):
    """删除测试用户参数"""

    pks: list[int] = Field(description='测试用户 ID 列表')


class GetTestUserDetail(TestUserSchemaBase):
    """测试用户详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
