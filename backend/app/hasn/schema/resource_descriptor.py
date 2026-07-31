"""AI-Native 应用资源描述符（Resource Descriptor）契约（doc31 §2.1，云端权威）。

每个 AI-Native 应用在其 App manifest 的 `resources[]` 声明它产出的资源长什么样、URI 怎么拼、
详情页怎么开、完成卡怎么显示。完成卡 / 工作会话资源栏 / 分享卡 / URI 解析 / 详情跳转全部从这份
声明派生——新应用只声明 descriptor，webui 零改代码（终结 deck 专属硬编码链路）。

事实源：docs/hasn-node设计文档/14-AI-Native应用平台/31-…设计.md §2；实施/32 RC-P0。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from backend.app.hasn.schema.hasn_artifacts import ArtifactKind
from backend.common.schema import SchemaBase


@dataclass(frozen=True)
class ArtifactRegistration:
    """register-on-write 的登记结果（doc36 §3.1）：`artifact_id` 给审计用，`resource_uri` 给分身打开用。

    `artifact_id` 可以为 `None`——descriptor 已解析、URI 已算出，但落库失败（登记是 best-effort，
    只 warn 不抛）。此时**仍然要把 URI 交给分身**：URI 是 `(uri_domain, server_id)` 的纯函数，业务写
    已经成功、资源真实存在且能打开（打开走资源自身的云端权威，不依赖 `hasn_artifacts` 行），登记失败
    只是「可见性的账」没记上。这时候不返 URI，分身既拿不到地址、又查不到产物（没登记），凭空双输。
    """

    artifact_id: str | None
    resource_uri: str


# open.mode 三枚举覆盖全部现实打开形态（doc31 §2.2）：
#   internal_route  有 /:id 详情路由的应用（reel/knowledge/creator…）
#   native_window   独立原生窗口应用（deck/design）
#   entry_query     单入口 / tab、无 /:id 段的应用（imagelab ?project= / quant ?backtest= 等）
ResourceOpenMode = Literal['internal_route', 'native_window', 'entry_query']

# 表面（doc09 §2）：同一个页面可以显示在哪。`surfaces` 只表达**显式排除**，不表达默认——
# 缺省即三者全允许（webui 已把「详情页必须自适应宽度」定成硬约束，不该再逐个声明）。
ResourceSurface = Literal['main', 'window', 'side']


class ResourceOpen(SchemaBase):
    """资源打开语义（descriptor.open）。按 mode 决定 webui `resolveHasnUri` 如何分发。"""

    mode: ResourceOpenMode = Field(description='打开模式：internal_route | native_window | entry_query')
    # internal_route 必填：含 :id 或 {id} 占位（如 /apps/reel/projects/:id）
    route_template: str | None = Field(None, description='internal_route 内部路由模板（含 :id/{id} 占位）')
    # native_window 可选：**业务视图注册表 id**（doc09 §5.4，如 deck / design / media-preview）。
    #
    # 由封闭 Literal['deck','design'] 放开为自由字符串：云端只存 id、不认识具体窗，合法性由
    # **前端注册表**校验——未注册的 id 一律按「无 window」处理并 warn，绝不开一扇空窗（零 fake）。
    # 这样新增一类业务视图**不需要动云端 schema**。
    window: str | None = Field(None, description='native_window 业务视图注册表 id（如 deck/design/media-preview）')
    # entry_query 必填：单入口路由 + query key（id 经 ?query_key=id 透传）
    entry_route: str | None = Field(None, description='entry_query 单入口路由（如 /apps/imagelab）')
    query_key: str | None = Field(None, description='entry_query 透传 id 的 query 键（如 item）')
    # 可选·回溯边界（doc09 §3 回落链第 2 级）：独立窗/侧窗里「最多退回到哪一层」。
    # 不声明时 webui 回落到该路由所属应用的 entry_route——「独立窗开社区帖子最多退回社区首页」
    # 因此零配置成立，绝大多数应用不需要填这个字段。
    root_route: str | None = Field(None, description='独立窗/侧窗的回溯边界路由（缺省取应用 entry_route）')
    # 可选·显式排除某些表面（如超宽仪表盘排除 side）。缺省 None = 三者全允许。
    surfaces: list[ResourceSurface] | None = Field(
        None, description='允许的表面（缺省全允许）：main | window | side'
    )

    @model_validator(mode='after')
    def _check_by_mode(self) -> ResourceOpen:
        if self.mode == 'internal_route':
            if not self.route_template:
                raise ValueError('internal_route 必须声明 route_template')
            if ':id' not in self.route_template and '{id}' not in self.route_template:
                raise ValueError('internal_route 的 route_template 必须含 :id 或 {id} 占位')
        elif self.mode == 'native_window':
            # 只校验「非空」——具体 id 是否已注册由前端注册表判定（云端不该知道有哪些业务视图）。
            if not (self.window or '').strip():
                raise ValueError('native_window 必须声明 window（业务视图注册表 id）')
        elif self.mode == 'entry_query':
            if not self.entry_route or not self.query_key:
                raise ValueError('entry_query 必须声明 entry_route 与 query_key')
        if self.root_route is not None and not self.root_route.startswith('/'):
            raise ValueError('root_route 必须是以 / 开头的应用内路由')
        if self.surfaces is not None and not self.surfaces:
            raise ValueError('surfaces 声明了就不能为空列表（不填即表示全允许）')
        return self


class ResourceCard(SchemaBase):
    """资源完成卡显示声明（descriptor.card）。标题 = "{verb}做好了"，主按钮 = action_label。"""

    verb: str = Field(description='资源名词（完成卡标题 = "{verb}做好了"，如 演示文稿/短视频）')
    action_label: str = Field(description='primary_action 主按钮文案（如 打开演示文稿）')
    # 可选：从产物 metadata 取哪些字段进卡摘要
    summary_fields: list[str] = Field(default_factory=list, description='摘要取用的产物 metadata 字段名')

    @model_validator(mode='after')
    def _non_empty(self) -> ResourceCard:
        if not (self.verb or '').strip():
            raise ValueError('card.verb 不能为空')
        if not (self.action_label or '').strip():
            raise ValueError('card.action_label 不能为空')
        return self


class ResourceDescriptor(SchemaBase):
    """一个应用产出的一类资源的描述符（manifest.resources[] 内一项）。"""

    resource_kind: str = Field(description='app 内唯一稳定键（如 deck.presentation / reel.project）')
    uri_domain: str = Field(description='hasn:// host+path 前缀，不含 /{id}（如 deck / reel/projects）')
    open: ResourceOpen = Field(description='打开语义')
    card: ResourceCard = Field(description='完成卡显示声明')
    # 可选：登记 hasn_artifacts 时的 artifact_kind。应用资源恒为 'resource'，缺省即 'resource'。
    #
    # 收 Literal 而非裸 str：以前 manifest 里写 'vidoe' 拼错不会在校验时红，一路静默落成
    # 'other'（doc35 §1.5 的隐患之一）。现在拼错在**注册期**就炸。
    artifact_kind: ArtifactKind | None = Field(
        None, description='登记 hasn_artifacts 的 artifact_kind（应用资源恒为 resource，缺省即 resource）'
    )
    # 可选·多资源应用的子类选择键（doc31 §2.1 扩展，RC-P6/doc31-A）：
    # 单类资源应用（deck/reel/design/knowledge…）不声明 ref_type——origin_ref=resource:{app}:{id}，
    # 整段 {id} 即云端资源 id。多类资源应用（如 plan：目标/计划分居 hasn://plan/goals 与 plan/plans）
    # 的 origin_ref=resource:{app}:{ref_type}:{id}（如 resource:plan:goal:5），据 ref_type 段选中本
    # descriptor 并剥前缀取 id。**opt-in**：只有声明了 ref_type 的应用进入「多资源模式」，其余保持整段作 id
    # 的历史行为（design 的 local_ref='proj:v2' 因未声明 ref_type 不受影响）。
    ref_type: str | None = Field(None, description='多资源应用 origin_ref 的子类选择键（如 plan 的 goal/plan）')

    def build_uri(self, server_id: str | int) -> str:
        """拼这条资源的 `hasn://` 地址 —— **全仓唯一的 URI 拼接点**（doc36 §3.1）。

        写路径（`record_app_resource_artifact` 登记）与读路径（`_kb_dict` 等投影）都必须调它。
        别处再拼一次 `f'hasn://{...}/{...}'` 就是第 N 处字面量——doc36 §1.3 盘出的五处 deck 域
        字面量、以及「manifest 声明了却和 doc08 对不上」的漂移，全是这么来的。

        `server_id` 必须是**云端权威 id**（Core-08 铁律：本地 id 永不上 URI，否则换设备 / 分享
        给别人就解析不开）。
        """
        return f'hasn://{self.uri_domain}/{server_id}'

    @model_validator(mode='after')
    def _check_uri_domain(self) -> ResourceDescriptor:
        domain = (self.uri_domain or '').strip()
        if not domain:
            raise ValueError('uri_domain 不能为空')
        if 'hasn://' in domain:
            raise ValueError('uri_domain 不含 scheme（不要写 hasn://）')
        if domain.startswith('/'):
            raise ValueError('uri_domain 不含前导斜杠')
        if not (self.resource_kind or '').strip():
            raise ValueError('resource_kind 不能为空')
        return self


class ResourceDomainInfo(SchemaBase):
    """资源域目录（**给分身看**的投影，与 `ResourceRoute` 同源不同投影，doc36 §5.2）。

    分身不需要知道「用哪个路由模板打开」——那是 webui 的事（`ResourceRoute` 干的）；分身要知道的是
    「系统里有哪些资源类型、URI 长什么样、归哪个应用」。故本投影只给身份与地址形状，不给路由细节。
    """

    app_id: str = Field(description='应用 id（如 knowledge）')
    resource_kind: str = Field(description='应用内资源类型（如 knowledge.base）')
    uri_domain: str = Field(description='hasn:// host+path 前缀，不含 /{id}（如 knowledge/kbs）')
    uri_example: str = Field(description='URI 形状示例（如 hasn://knowledge/kbs/{id}）——直接给形状，别让分身猜')
    label: str | None = Field(None, description='人话资源名（如 知识库）')

    @classmethod
    def from_descriptor(cls, app_id: str, descriptor: ResourceDescriptor) -> ResourceDomainInfo:
        # label 取 `card.verb`：字段名叫 verb，值却是**名词性资源名**（16 个 builtin 声明全是
        # 「知识库」「演示文稿」「短视频」这类，无一动词）——完成卡标题 "{verb}做好了" 正需名词。
        # 既有 `resource_kind_labels()`（同取 card.verb，docstring 直呼「人话展示名」）已是先例，
        # 此处同源，不另立第二份展示名。
        return cls(
            app_id=app_id,
            resource_kind=descriptor.resource_kind,
            uri_domain=descriptor.uri_domain,
            # 用 build_uri 生成示例，形状与真实登记出的地址同源——手写 f'hasn://{domain}/{{id}}'
            # 就是又一处会漂移的字面量（doc36 §3.1 唯一拼接点铁律）。
            uri_example=descriptor.build_uri('{id}'),
            label=descriptor.card.verb,
        )


class ResourceRoute(SchemaBase):
    """资源路由读模型（从 descriptor 投影出的扁平表，随 catalog 下发 daemon/webui）。

    webui `registerResourceRoutes` 据此把 `hasn://{uri_domain}/{id}` 解析到内部路由/独立窗口/单入口。
    """

    app_id: str = Field(description='应用 id')
    uri_domain: str = Field(description='hasn:// host+path 前缀（如 reel/projects）')
    open_mode: ResourceOpenMode = Field(description='打开模式')
    route_template: str | None = Field(None, description='internal_route 内部路由模板')
    window: str | None = Field(None, description='native_window 业务视图注册表 id')
    entry_route: str | None = Field(None, description='entry_query 单入口路由')
    query_key: str | None = Field(None, description='entry_query 透传 id 的 query 键')
    root_route: str | None = Field(None, description='独立窗/侧窗回溯边界（缺省取应用 entry_route）')
    surfaces: list[ResourceSurface] | None = Field(None, description='允许的表面（缺省全允许）')

    @classmethod
    def from_descriptor(cls, app_id: str, descriptor: ResourceDescriptor) -> ResourceRoute:
        return cls(
            app_id=app_id,
            uri_domain=descriptor.uri_domain,
            open_mode=descriptor.open.mode,
            route_template=descriptor.open.route_template,
            window=descriptor.open.window,
            entry_route=descriptor.open.entry_route,
            query_key=descriptor.open.query_key,
            root_route=descriptor.open.root_route,
            surfaces=descriptor.open.surfaces,
        )
