from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class WorkbenchApp:
    id: str
    name: str
    icon: str
    description: str
    scope: tuple[str, ...]
    entry_route: str
    install_policy: str
    collaboration_mode: str = 'none'
    requires_role: str | None = None
    health_check: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # embedded_desktop AI-Native 应用新增字段（设计 §6.1/§7.2）：
    # execution_mode 在 WorkbenchApp 层声明（doc02 §1）；ui_kind/window_url/window_origin
    # 描述本地嵌入式 UI 句柄（窗口经 daemon 同源代理，非 sidecar 真实端口）。
    execution_mode: str = 'cloud'
    ui_kind: str | None = None
    window_url: str | None = None
    window_origin: str | None = None

    def to_manifest(self, *, workspace_kind: str | None = None) -> dict[str, Any]:
        data = {
            'id': self.id,
            'name': self.name,
            'icon': self.icon,
            'description': self.description,
            'scope': list(self.scope),
            'collaboration_mode': self.collaboration_mode,
            'entry_route': self.entry_route,
            'install_policy': self.install_policy,
            'requires_role': self.requires_role,
            # 三处端到端同步（§6.1 注）：云端 manifest → daemon 透传 → WebUI 接口类型。
            'execution_mode': self.execution_mode,
            'ui_kind': self.ui_kind,
            'window_url': self.window_url,
            'window_origin': self.window_origin,
        }
        if workspace_kind and self.health_check:
            data['health'] = self.health_check({'workspace_kind': workspace_kind})
        return data


class WorkbenchAppRegistry:
    def __init__(self) -> None:
        self._apps: dict[str, WorkbenchApp] = {}

    @classmethod
    def default(cls) -> WorkbenchAppRegistry:
        registry = cls()
        registry.register(
            WorkbenchApp(
                id='knowledge',
                name='知识库',
                icon='book-open',
                description='在当前工作空间管理知识库、搜索与审计。',
                scope=('personal', 'enterprise'),
                collaboration_mode='workspace_shared',
                entry_route='/workbench/apps/knowledge',
                install_policy='auto',
            )
        )
        registry.register(
            WorkbenchApp(
                id='community',
                name='社区',
                icon='users-round',
                description='人和 Agent 共同创作、互动和建立信任的公共空间',
                scope=('personal', 'enterprise'),
                collaboration_mode='workspace_shared',
                entry_route='/community',
                install_policy='auto',
            )
        )
        # Presenton 演示文稿（embedded_desktop AI-Native，设计 §7.2）。延迟导入避免循环依赖。
        from backend.app.hasn.service.ai_native_builtin_presentation import build_presentation_workbench_app

        registry.register(build_presentation_workbench_app())
        # 自研演示文稿 deck（local_tool AI-Native，模块 17 §6.2；与 Presenton 研发期并存，install_policy=manual）。
        from backend.app.deck.manifest import build_deck_workbench_app

        registry.register(build_deck_workbench_app())
        # 任务系统 hasn_task（local_tool AI-Native，模块 12 设计 06 §4）。延迟导入避免循环依赖。
        from backend.app.hasn_task.service.ai_native_manifest import build_hasn_task_workbench_app

        registry.register(build_hasn_task_workbench_app())
        return registry

    def register(self, app: WorkbenchApp) -> None:
        self._apps[app.id] = app

    def get(self, app_id: str) -> WorkbenchApp:
        return self._apps[app_id]

    def list(self, workspace_kind: str | None = None) -> list[WorkbenchApp]:
        apps = list(self._apps.values())
        if workspace_kind:
            apps = [app for app in apps if workspace_kind in app.scope]
        return apps

    def auto_install_apps(self, workspace_kind: str) -> list[WorkbenchApp]:
        return [app for app in self.list(workspace_kind) if app.install_policy == 'auto']


workbench_app_registry = WorkbenchAppRegistry.default()
