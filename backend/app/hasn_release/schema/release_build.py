from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ReleaseBuildSchemaBase(SchemaBase):
    """CI 构建任务（GitHub Actions 构建进度追踪）基础模型"""
    ref: str = Field(description='构建 ref（branch 或 tag，如 main / v1.2.0）')
    channel: str = Field(description='目标渠道 (stable:稳定版:green/beta:内测版:orange)')
    status: str = Field(description='构建状态 (queued:排队中:gray/building:构建中:blue/success:成功:green/failed:失败:red)')
    version: str | None = Field(None, description='产出版本号（构建成功后回填）')
    github_run_id: str | None = Field(None, description='GitHub Actions run id')
    github_run_url: str | None = Field(None, description='GitHub Actions run 页面链接')
    triggered_by: str | None = Field(None, description='触发者（管理员用户名/hasn_id）')
    error_message: str | None = Field(None, description='失败原因（status=failed 时）')


class CreateReleaseBuildParam(ReleaseBuildSchemaBase):
    """创建CI 构建任务（GitHub Actions 构建进度追踪）参数"""


class UpdateReleaseBuildParam(ReleaseBuildSchemaBase):
    """更新CI 构建任务（GitHub Actions 构建进度追踪）参数"""


class DeleteReleaseBuildParam(SchemaBase):
    """删除CI 构建任务（GitHub Actions 构建进度追踪）参数"""

    pks: list[int] = Field(description='CI 构建任务（GitHub Actions 构建进度追踪） ID 列表')


class GetReleaseBuildDetail(ReleaseBuildSchemaBase):
    """CI 构建任务（GitHub Actions 构建进度追踪）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
