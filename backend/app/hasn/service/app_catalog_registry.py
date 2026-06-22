from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class App:
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
    # execution_mode 在 App 层声明（doc02 §1）；ui_kind/window_url/window_origin
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


class AppCatalogRegistry:
    def __init__(self) -> None:
        self._apps: dict[str, App] = {}

    @classmethod
    def default(cls) -> AppCatalogRegistry:
        registry = cls()
        registry.register(
            App(
                id='knowledge',
                name='知识库',
                icon='brand-knowledge',
                description='把零散的资料、笔记、文档汇成你的私人知识大脑——分身随时检索、引用、问答，越用越懂你。',
                scope=('personal', 'enterprise'),
                collaboration_mode='workspace_shared',
                entry_route='/apps/knowledge',
                install_policy='auto',
            )
        )
        registry.register(
            App(
                id='community',
                name='社区',
                icon='brand-community',
                description='人与 AI 分身共创的公共广场——发现好内容、结识同好、关注互动，让分身替你经营存在感。',
                scope=('personal', 'enterprise'),
                collaboration_mode='workspace_shared',
                entry_route='/apps/community',
                install_policy='auto',
            )
        )
        # 自研演示文稿 deck（local_tool AI-Native，模块 17 §6.2；唯一默认演示文稿应用，install_policy=auto）。
        from backend.app.hasn_deck.manifest import build_deck_app

        registry.register(build_deck_app())
        # 任务系统 hasn_task（local_tool AI-Native，模块 12 设计 06 §4）。延迟导入避免循环依赖。
        from backend.app.hasn_task.service.ai_native_manifest import build_hasn_task_app

        registry.register(build_hasn_task_app())
        # 通用网页发布与分享 publish（local_tool AI-Native，模块 18）。延迟导入避免循环依赖。
        from backend.app.hasn_publish.manifest import build_publish_app

        registry.register(build_publish_app())
        # 获客 growth（cloud AI-Native，schema hasn_growth；install_policy=manual 非默认挂载）。延迟导入避免循环依赖。
        from backend.app.hasn_growth.manifest import build_growth_app

        registry.register(build_growth_app())
        # 创作运营 creator（cloud AI-Native，schema hasn_creator；install_policy=manual 非默认挂载）。延迟导入避免循环依赖。
        from backend.app.hasn_creator.manifest import build_creator_app

        registry.register(build_creator_app())
        # 自研设计系统 designsystem（local_tool AI-Native，schema hasn_designsystem；install_policy=manual
        # 非默认挂载——本期只铸 scope+注册 manifest，启动入口随 P8 webui+catalog 行落地）。延迟导入避免循环依赖。
        from backend.app.hasn_designsystem.manifest import build_designsystem_app

        registry.register(build_designsystem_app())
        # 视频生成 film（local_tool AI-Native，源自 VideoClaw；install_policy=manual 非默认挂载——
        # 本期 VC-P4 只铸 scope+注册 manifest，启动入口随 P8 webui+catalog 行落地）。延迟导入避免循环依赖。
        from backend.app.hasn_film.manifest import build_film_app

        registry.register(build_film_app())
        # 短视频合成 reel（local_tool AI-Native，源自 MoneyPrinterTurbo，模块 14 doc19；install_policy=manual
        # 非默认挂载——瘦引擎应用按需装，内容创作在创作运营完成）。延迟导入避免循环依赖。
        from backend.app.hasn_reel.manifest import build_reel_app

        registry.register(build_reel_app())
        # 会议副驾 copilot（local_tool AI-Native，无 Agent 工具——走工作会话派发；schema hasn_copilot；
        # install_policy=manual 非默认挂载，完整实时副驾依赖桌面端原生隐身/音频）。延迟导入避免循环依赖。
        from backend.app.hasn_copilot.manifest import build_copilot_app

        registry.register(build_copilot_app())
        # 规划与目标管理 plan（local_tool AI-Native，schema hasn_plan，模块 19；install_policy=manual
        # 非默认挂载——PLAN-P1 只铸 scope+注册 manifest，启动入口随 webui+catalog 行落地）。延迟导入避免循环依赖。
        from backend.app.hasn_plan.manifest import build_plan_app

        registry.register(build_plan_app())
        # 金融数据 finance（cloud AI-Native，schema hasn_finance，模块 24；install_policy=manual
        # 非默认挂载——纯云端只读数据应用，14 工具经 finance_provider → finance-data-service 取数）。延迟导入避免循环依赖。
        from backend.app.hasn_finance.manifest import build_finance_app

        registry.register(build_finance_app())
        # 量化交易 quant（cloud-brokered AI-Native，schema hasn_quant，模块 14 doc23；install_policy=manual
        # 非默认挂载——专业量化工作台，hasn.quant.* 工具经 quant_engine_provider → quant-engine-service 跑回测。
        # 本期 P0–P5 回测研究平台零资金风险；实盘线 P6+ 受 P0-闸1 产品/法务硬闸）。延迟导入避免循环依赖。
        from backend.app.hasn_quant.manifest import build_quant_app

        registry.register(build_quant_app())
        return registry

    def register(self, app: App) -> None:
        self._apps[app.id] = app

    def get(self, app_id: str) -> App:
        return self._apps[app_id]

    def list(self, workspace_kind: str | None = None) -> list[App]:
        apps = list(self._apps.values())
        if workspace_kind:
            apps = [app for app in apps if workspace_kind in app.scope]
        return apps

    def auto_install_apps(self, workspace_kind: str) -> list[App]:
        return [app for app in self.list(workspace_kind) if app.install_policy == 'auto']


app_catalog_registry = AppCatalogRegistry.default()
