"""项目挂靠工具说明必须跟随真实注册表，禁止维护过期的手工资源域清单。"""

from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry  # noqa: F401
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.app.mcp.tools.project import PROJECT_TOOLS


def test_link_description_is_derived_from_registered_domains() -> None:
    """每个当前可挂靠域都出现在工具说明中，未使用写死的支持清单。"""
    tool = next(tool for tool in PROJECT_TOOLS if tool.name == 'hasn.project.link')
    description = tool.description
    domains = project_linkage_registry.linkable_domains()

    assert domains
    assert '当前可显式挂靠的资源域由平台注册表实时派生' in description
    for domain in domains:
        assert f'hasn://{domain}/{{id}}' in description
