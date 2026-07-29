"""创作运营容器的平台项目挂靠注册。

创作业务项目仍使用自身 bigint `project.id` 管理账号、内容和审核；`platform_project_id`
仅用于把整个运营单元归集到平台项目，绝不复用前者冒充平台项目身份。
"""

from backend.app.hasn_creator.model.project import Project
from backend.app.hasn_project.service.project_linkage_registry import LinkageAdapter, project_linkage_registry

project_linkage_registry.register(
    LinkageAdapter(
        domain='creator/projects',
        model=Project,
        id_column='id',
        owner_column='assignee',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
        app_id='creator',
        kind='creator_project',
        title_column='name',
    )
)
