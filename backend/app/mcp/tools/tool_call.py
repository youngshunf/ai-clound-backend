"""通用调用元工具 `hasn.cloud.tool.call`（设计 03 §9）。

function-calling Runtime 只能发起 `tools/list` 中的工具调用。为在不全量暴露
（恢复 03 §1 渐进式）的前提下兑现 §7「直接调用任意 canonical name」，bootstrap
清单放一个通用调用元工具，由它把调用转发给任意已注册工具。

权限/审计/维度② 一律落**内层**工具（委托回 server.call_tool 走统一调用管线，
设计 04）；本元工具自身透明。参数 schema 校验失败时回吐内层完整 schema
（schema-on-error，见 §9.4），由 P3 实现。
"""
from __future__ import annotations

import json
import logging

from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from backend.app.mcp.canonical import schema_hash
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.tools.base import BaseTool

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext
    from backend.app.mcp.server import HasnCloudMcpServer

logger = logging.getLogger(__name__)

# 不可被 tool.call 转发的元工具（防自循环 + 语义无意义）。
_NON_DISPATCHABLE = frozenset({
    "hasn.cloud.tool.call",
    "hasn.local.tool.call",
    "hasn.cloud.tool.search",
    "hasn.local.tool.search",
    "hasn.tool.search",
})


def _declared_types(schema: Any) -> list[str]:
    """取 schema 声明的 JSON 类型列表（兼容 `"integer"` 与 `["string", "null"]` 两种写法）。"""
    if not isinstance(schema, dict):
        return []
    declared = schema.get("type")
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list):
        return [t for t in declared if isinstance(t, str)]
    return []


def _matches_declared(value: Any, types: list[str]) -> bool:
    """值是否已经满足任一声明类型——满足就绝不改动它（只治真正错型的，不做无谓转换）。"""
    for t in types:
        if t == "null" and value is None:
            return True
        if t == "boolean" and isinstance(value, bool):
            return True
        # bool 是 int 子类，判数值类型时必须先排除，否则 True 会被当成合法 integer。
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if t == "string" and isinstance(value, str):
            return True
        if t == "array" and isinstance(value, list):
            return True
        if t == "object" and isinstance(value, dict):
            return True
    return False


def _parse_json_container(text: str, types: list[str]) -> Any | None:
    """把 JSON 字符串还原成 array/object；解析失败或还原出的类型不在声明内 → None（不采用）。"""
    try:
        decoded = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(decoded, list) and "array" in types:
        return decoded
    if isinstance(decoded, dict) and "object" in types:
        return decoded
    return None


def _coerce_scalar(value: Any, types: list[str]) -> Any | None:
    """标量还原：字符串→整数/浮点/布尔、数值→字符串。转不动返回 None（表示不采用）。"""
    if isinstance(value, str):
        text = value.strip()
        if "integer" in types:
            try:
                return int(text)
            except ValueError:
                pass
        if "number" in types:
            try:
                return float(text)
            except ValueError:
                pass
        if "boolean" in types and text.lower() in ("true", "false"):
            return text.lower() == "true"
        return None
    # bool 不参与转字符串，否则 True 会变成 'True' 这种谁都不想要的值。
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "string" in types:
        return str(value)
    return None


def coerce_params_to_schema(schema: Any, value: Any) -> Any:
    """按 JSON Schema 宽容还原「被 Runtime/LLM 序列化过」的入参，转不动一律原样返回。

    治的是一类跨 Runtime 的稳定行为：function-calling 侧常把嵌套容器序列化成 JSON 字符串
    （``pages='[{"position":0,...}]'``）、把数值当字符串填（``position="0"``、``deck_id=21``）。
    `_extract_params` 只还原了**顶层** params 这一层，字段值这一层此前无人还原，于是凡是
    input_schema 里带 integer/array/object 的工具，经本元工具转发时一律 `input_validation_failed`
    ——本仓 126 个注册工具里有 33 个连必填字段都是非 string，deck 的
    `page.write` / `page.write_batch` / `outline.set` 是撞得最狠的那三个。

    三条保守边界，保证「宽容接收」不退化成「猜」：
    - 值已满足任一声明类型 → 原样不动；
    - 字符串还原成容器时，还原结果的类型必须落在声明内才采用；
    - 任何转不动的情况都**原样返回**，交给 `_validate_params` 如实报 invalid（零 fake，
      错型仍然报错，只是不再把「本可还原的形态」也一并判死）。

    纯函数、不改入参（容器一律新建），确保 ask_gate 的 args_hash 对同一逻辑调用稳定。
    """
    types = _declared_types(schema)

    # object：递归还原已声明的属性；未声明字段原样透传（与 input_binding 的附加式取向一致）。
    if isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(properties, dict):
            return value
        return {
            key: (coerce_params_to_schema(properties[key], item) if key in properties else item)
            for key, item in value.items()
        }

    # array：逐元素按 items 还原。
    if isinstance(value, list):
        items = schema.get("items") if isinstance(schema, dict) else None
        if not isinstance(items, dict):
            return value
        return [coerce_params_to_schema(items, item) for item in value]

    if not types or _matches_declared(value, types):
        return value

    # 字符串承载的容器：还原后继续向下递归（内层同样可能被序列化/错型）。
    if isinstance(value, str):
        container = _parse_json_container(value.strip(), types)
        if container is not None:
            return coerce_params_to_schema(schema, container)

    coerced = _coerce_scalar(value, types)
    return value if coerced is None else coerced


# ── 校验失败时的可照抄修正（配合 schema-on-error）─────────────────────────────────
# 占位值按声明类型给，**故意长得像占位符**（尖括号包裹），避免分身把示例值当成真实业务值抄进去。
_TYPE_PLACEHOLDER: dict[str, Any] = {
    "string": "<字符串>",
    "integer": 0,
    "number": 0,
    "boolean": False,
    "array": [],
    "object": {},
    "null": None,
}


def _placeholder_for(prop_schema: Any) -> Any:
    """按字段声明类型给一个占位值（多类型取第一个非 null 的；未声明按字符串）。"""
    for declared in _declared_types(prop_schema):
        if declared != "null" and declared in _TYPE_PLACEHOLDER:
            return _TYPE_PLACEHOLDER[declared]
    return _TYPE_PLACEHOLDER["string"]


def _example_call(tool_name: str, schema: Any) -> dict[str, Any]:
    """按内层 schema 的 required 生成一次**最小可照抄**的 tool.call 调用。

    只放必填字段：示例的作用是示范 ``{tool, params}`` 的包裹结构与字段该落在哪一层，
    不是替分身把业务值想好。schema 没声明 required 时给空 params（结构本身仍然有示范价值）。
    """
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = schema.get("required") if isinstance(schema, dict) else None
    properties = properties if isinstance(properties, dict) else {}
    required = required if isinstance(required, list) else []
    params = {key: _placeholder_for(properties.get(key)) for key in required if isinstance(key, str)}
    return {"tool": tool_name, "params": params}


def _how_to_fix(schema: Any, missing: list[str]) -> str:
    """一句话说清这次该怎么改——内层工具自己有 ``name`` 字段时额外点破那个撞键陷阱。"""
    base = "把目标工具的入参放进 params 对象，用 tool 指定要调用哪个工具（见 example，可逐字照抄结构）。"
    properties = schema.get("properties") if isinstance(schema, dict) else None
    has_name_field = isinstance(properties, dict) and "name" in properties
    if has_name_field and "name" in missing:
        return (
            base
            + " ⚠️ 本工具自己有 name 字段：它必须放在 params 里，"
            "顶层的 name 会被当成「要调用哪个工具」而不是业务名称。"
        )
    return base


class ToolCallTool(BaseTool):
    """把调用转发给任意 canonical 工具的通用调用元工具。"""

    def __init__(self, server: HasnCloudMcpServer) -> None:
        self._server = server

    @property
    def source(self) -> str:
        return "platform"

    @property
    def name(self) -> str:
        return "hasn.cloud.tool.call"

    @property
    def description(self) -> str:
        return (
            "调用任意云端 MCP 工具：tool.call(tool, params)——目标工具名放 `tool`，业务入参放 `params`。"
            "已知 canonical name 可直接调用，"
            "无需先 tool.search；参数错误会返回该工具的完整 schema 供修正后重试。"
            "params 是目标工具的入参对象，键即目标工具 input_schema 的字段——"
            "例如调用 hasn.community.search 时传 params={\"query\": \"关键词\", \"limit\": 10}。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        # params 必须声明为「开放对象」（additionalProperties: true）并在描述里给出示例，
        # 否则 function-calling Runtime / LLM 会把「无字段的 object」当成「不接受任何字段」，
        # 只能产出 params={}（参数在 LLM 侧就丢了，根本到不了云端）。详见 tool.call 的字段透传修复。
        # 顶层放开 additionalProperties，容忍部分 Runtime 把内层参数平铺到顶层而非包进 params。
        # ⚠️ 只暴露 **一个** 承载目标工具名的键。服务端 `_resolve_target_name` 仍然兼容接收
        # `name`（老 Runtime 与存量调用方），但**绝不把它列进 properties**——2026-08-26 线上回归：
        # 曾同时列出 `tool` 与 `name`、两者描述都写「目标工具 canonical name」，模型于是**把工具名
        # 填了两遍**（`{"name":"hasn.designsystem.get","tool":"hasn.designsystem.get"}`）而把
        # `params` 整个丢掉，designsystem / knowledge 等域一起报 missing。两个语义等价的键摆在
        # 模型面前，它不会二选一，它会都填。
        return {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": (
                        "要调用哪个工具，填 canonical name，如 hasn.community.create_post。"
                        "这里**只放工具名**，业务参数一律放 params。"
                    ),
                },
                "params": {
                    "type": ["object", "string"],
                    "description": (
                        "目标工具的**全部**入参，一个对象。键=目标工具 input_schema 的字段，"
                        "例如调 hasn.designsystem.get 时传 params={\"design_system_id\": 157}。"
                        "⚠️ 业务参数只能放这里，放到外层不会生效；目标工具自己有 name 字段时"
                        "（designsystem.create/save、task.*、workflow.* 等），那个 name 也放在 params 里。"
                        "不确定字段就先用 hasn.cloud.tool.search 查目标工具 schema。"
                    ),
                    "additionalProperties": True,
                },
            },
            # ⚠️ **不设 `required`**，这不是疏漏。MCP SDK 在把请求交给我们的 handler 之前，就先拿这份
            # schema `jsonschema.validate` 了原始 wire 入参（`mcp/server/lowlevel/server.py`）——凡是
            # 写进 `required` 的键，`_resolve_target_name` 的 `name` 兼容分支与 `_extract_params` 的
            # 平铺兜底就**一起变成死代码**，根本走不到。2026-08-26 线上实测：那次把 `required` 钉成
            # `["tool", "params"]` 后，存量调用方发的 `{"name": ..., "params": ...}` 全被 SDK 判
            # `Input validation error: 'tool' is a required property`，分身连撞 3 次触发 Runtime 侧
            # MCP 熔断，被告知「云端 MCP 不可用」，整条云端通道在它眼里雪崩——而云端一直是好的。
            # 分工因此定死：**schema 只负责宣传（properties + description），判定一律落 handler**。
            # handler 的错误回吐内层完整 schema（§9.4 schema-on-error）并给出可照抄形状，SDK 那句
            # 只有一行、不带 schema，还白烧一次熔断次数。
            "additionalProperties": True,
        }

    async def execute(
        self,
        agent_context: AgentContext,
        arguments: dict[str, Any],
    ) -> Any:
        name, target_key = self._resolve_target_name(arguments)

        params = self._extract_params(arguments, target_key=target_key, target_name=name)

        if name in _NON_DISPATCHABLE or name == self.name:
            raise McpToolError(McpErrorCode.DIRECT_CALL_DENIED, f"tool.call cannot dispatch meta tool: {name}")

        # 包装器本身始终保留，但内层业务工具必须先过本次工作会话白名单；判定先于注册表查询与
        # schema-on-error，避免未授权工具借包装器泄露存在性或 schema。
        from backend.app.mcp.trust_gate import is_session_tool_allowed

        if not is_session_tool_allowed(agent_context, name):
            raise McpToolError(McpErrorCode.TOOL_NOT_ALLOWED, f'工作会话未授权调用工具: {name}')

        # 解析内层工具（含迁移别名）。App 工具已由外层 call_tool 的 _load_app_tools 载入。
        inner_tool = self._server.tool_registry.get_tool(name)
        if inner_tool is None:
            raise self._unknown_target_error(name, target_key)

        # 字段值级宽容还原：`_extract_params` 只把**顶层** params 从字符串还原成 dict，
        # 字段值本身被 Runtime/LLM 序列化（position="0"、pages='[{...}]'）时仍会被下面的
        # 严格类型校验挡掉。此处按内层 schema 再还原一层，转不动的原样保留、照旧报错。
        params = coerce_params_to_schema(inner_tool.input_schema, params)

        # schema-on-error（§9.4）：仅参数 schema 校验失败时回吐内层完整 schema 供修正；
        # 业务失败（维度② 不可达、额度等）由内层工具透传，不附 schema。
        validation_error = self._validate_params(inner_tool, params)
        if validation_error is not None:
            return validation_error

        # 委托统一调用管线：维度① 三态闸门 + 维度② + 审计全落内层。
        # 传还原后的 params——内层 handler 拿到的必须与通过校验的是同一份，否则
        # `int(args['deck_id'])` / `list(args['pages'])` 这类取值会拿到字符串再炸一次。
        return await self._server.call_tool(agent_context, name, params)

    @staticmethod
    def _resolve_target_name(arguments: dict[str, Any]) -> tuple[str, str]:
        """解析「要调哪个工具」→ ``(目标工具名, 承载它的键名)``。

        ``tool`` 优先于 ``name``：``name`` 这个键与不少内层工具**自己的** ``name`` 入参同名
        （designsystem.save 的展示名、task/workflow 的名称），历史上只认 ``name`` 时，分身想传业务
        name 就会把它顶到目标工具名的位置——实测报出 ``Tool not found: 昆明即时宠物零售 · 专业猫舍设计系统``。
        返回承载键是为了让 :meth:`_extract_params` 只排除**真正**用作工具名的那一个键，把另一个
        （业务 ``name``）如实归还内层。
        """
        for key in ("tool", "name"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), key
        raise McpToolError(
            McpErrorCode.INVALID_CALL_ARGUMENTS,
            'tool.call: 缺少目标工具名。用 tool="hasn.<域>.<动作>" 指定要调用哪个工具，'
            "业务入参放进 params。先用 hasn.cloud.tool.search 查到 canonical name。",
        )

    def _unknown_target_error(self, name: str, target_key: str) -> McpToolError:
        """目标工具查不到时的错误——要能分清「工具真的不存在」和「业务字段被当成了工具名」。

        判据是这个值**像不像** canonical name（``hasn.`` 前缀 + 点分段）。不像时几乎一定是后者，
        此时报 ``TOOL_NOT_FOUND`` 会把分身推向「换个工具名再试」的死路（实测连撞两次，触发
        runtime 的 repeated_exact_failure_warning），所以改报入参错误并直接给出正确形状。
        """
        looks_canonical = name.startswith("hasn.") and name.count(".") >= 2
        if looks_canonical:
            return McpToolError(McpErrorCode.TOOL_NOT_FOUND, f"Tool not found: {name}")
        return McpToolError(
            McpErrorCode.INVALID_CALL_ARGUMENTS,
            f'tool.call: {target_key}="{name}" 不是工具名（canonical name 形如 hasn.<域>.<动作>）。'
            f"若它其实是目标工具的业务字段，请用 tool 指定目标工具、把它放进 params，例如："
            f'{{"tool": "hasn.<域>.<动作>", "params": {{"{target_key}": "{name}", ...}}}}。'
            "不确定 canonical name 就先调 hasn.cloud.tool.search。",
        )

    def _extract_params(
        self, arguments: dict[str, Any], *, target_key: str = "name", target_name: str = ""
    ) -> dict[str, Any]:
        """把 Runtime 传来的 params 归一化成 dict，兼容三种到达形态。

        不同 function-calling Runtime 对「内层工具入参」的承载方式不一致：
        1. 对象（正常）：``{"tool": ..., "params": {"query": "..."}}``；
        2. JSON 字符串：``{"tool": ..., "params": "{\"query\": \"...\"}"}``（部分 Runtime 把对象序列化成串）；
        3. 平铺顶层：``{"tool": ..., "query": "..."}``（部分 Runtime 不会嵌套 params，直接铺在顶层）。

        服务端宽容接收，三种都还原成内层 params dict——避免「参数没透传到云端」。

        形态 3 只排除**实际承载目标工具名的那个键**（``target_key``）与 ``params``：另一个名字键若装的
        是业务值（``{"tool": "hasn.designsystem.save", "name": "我的设计系统", ...}``）必须如实归还内层，
        否则内层的 ``name`` 字段在平铺形态下**结构性不可达**——它会永远缺失，而分身怎么传都对不了。
        """
        raw = arguments.get("params")
        params: dict[str, Any] = {}
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                try:
                    decoded = json.loads(text)
                except (ValueError, TypeError) as exc:
                    raise self._params_json_error(text, exc) from exc
                if not isinstance(decoded, dict):
                    raise McpToolError(
                        McpErrorCode.INVALID_CALL_ARGUMENTS,
                        "tool.call: 'params' 必须是对象（或对象的 JSON 字符串），"
                        f"实际解析出 {type(decoded).__name__}。",
                    )
                params = decoded
        elif isinstance(raw, dict):
            params = dict(raw)
        elif raw is not None:
            raise McpToolError(
                McpErrorCode.INVALID_CALL_ARGUMENTS,
                f"tool.call: 'params' 必须是对象，实际是 {type(raw).__name__}。",
            )

        # 形态 3 兜底：params 为空但顶层带了非保留键 → 视为被平铺的内层入参。
        return params or self._flattened_params(arguments, target_key=target_key, target_name=target_name)

    @staticmethod
    def _flattened_params(arguments: dict[str, Any], *, target_key: str, target_name: str) -> dict[str, Any]:
        """形态 3：把平铺在顶层的内层入参收集起来（排除承载工具名的键与 ``params`` 自身）。

        只排除 ``target_key``——另一个名字键若装的是**业务值**必须归还内层；只有当它装的也是同一个
        工具名时才一并排除（两个键都填了目标，不是业务字段）。
        """
        skip = {target_key, "params"}
        for key in ("tool", "name"):
            if key != target_key and arguments.get(key) == target_name:
                skip.add(key)
        return {k: v for k, v in arguments.items() if k not in skip}

    @staticmethod
    def _params_json_error(text: str, exc: Exception) -> McpToolError:
        """``params`` 串不是合法 JSON 时的错误——把「坏在哪」和「多半为什么坏」都说清楚。

        此前这里报 ``TOOL_NOT_FOUND``，于是「我这段 JSON 写坏了」在分身眼里长成「这个工具不存在」，
        它只会去换工具名重试。实测某个分身的 designsystem.save 调用里，**41% 卡在这一条**——
        入参是几十 KB 的整包 HTML，经 tool.call 再套一层 JSON 后要一次无差错吐出 2 万～4.5 万字符的
        双重转义串，模型在中途漏了逗号/引号。所以除了位置，还要点破「入参太大」这个真因并给出出路。
        """
        position = getattr(exc, "pos", None)
        where = f"（第 {position} 个字符附近；本次 params 串长 {len(text)}）" if isinstance(position, int) else ""
        oversized = len(text) > 8000
        advice = (
            "入参过大时模型很难一次吐出完整的转义 JSON——改用该域的分片写入工具"
            "（先建壳拿 id，再逐块 put，最后 finalize），或把大段正文改用资产句柄传递，"
            "不要把整包内容塞进一次调用。"
            if oversized
            else "请检查引号、逗号与转义是否配对；也可以把 params 作为**对象**直接传，不必先序列化成字符串。"
        )
        return McpToolError(
            McpErrorCode.INVALID_CALL_ARGUMENTS,
            f"tool.call: 'params' 不是合法的 JSON{where}：{exc}。{advice}",
        )

    def _validate_params(self, inner_tool: BaseTool, params: dict[str, Any]) -> dict[str, Any] | None:
        """用内层工具 input_schema 校验 params；通过返回 None，失败返回 schema-on-error 信封。"""
        schema = inner_tool.input_schema or {}
        errors = sorted(
            Draft202012Validator(schema).iter_errors(params),
            key=lambda e: [str(p) for p in e.path],
        )
        if not errors:
            return None

        missing: list[str] = []
        invalid: dict[str, str] = {}
        for error in errors:
            path = ".".join(str(p) for p in error.path)
            if error.validator == "required":
                prop = error.message.split("'")[1] if "'" in error.message else error.message
                key = f"{path}.{prop}" if path else prop
                if key not in missing:
                    missing.append(key)
            else:
                invalid.setdefault(path or "(root)", error.message)

        # 服务端留痕：本分支是 `return` 而非 raise，且 tool.call 落在 `_DISPATCH_TOOL_NAMES` 里
        # （`_should_audit_call` 直接 False，审计按设计落内层）——可内层工具**压根没被调到**，
        # 于是分身在此卡住多少次，审计与日志两侧都一片空白。deck 三个写工具的字段值序列化
        # bug 能长期无声，正是因为这里不出声。按日志分级：分身可据 schema 修正后重试，记 warn。
        logger.warning(
            "[tool.call] 内层入参校验未通过 tool=%s missing=%s invalid=%s",
            inner_tool.name,
            missing,
            sorted(invalid),  # 只记字段名，不记值——入参可能含整页 HTML 等大段内容
        )

        envelope: dict[str, Any] = {
            "ok": False,
            "error": "input_validation_failed",
            "tool": inner_tool.name,
            "missing": missing,
            "invalid": invalid,
        }
        # schema 本身只回答「字段叫什么、什么类型」，不回答「我该把它放在哪一层」。实测分身
        # 拿着完整 schema 仍反复把内层字段平铺到 tool.call 顶层，因为整段回吐里没有一处示范过
        # {tool, params} 这个包裹结构。补一条能逐字照抄的最小调用，比再多回吐一遍 schema 有用。
        envelope["how_to_fix"] = _how_to_fix(schema, missing)
        envelope["example"] = _example_call(inner_tool.name, schema)
        envelope["input_schema"] = schema
        envelope["schema_hash"] = schema_hash(schema)
        return envelope
