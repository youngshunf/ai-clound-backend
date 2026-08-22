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
            "调用任意云端 MCP 工具：tool.call(name, params)。已知 canonical name 可直接调用，"
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
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "目标工具 canonical name，如 hasn.community.create_post",
                },
                "params": {
                    "type": ["object", "string"],
                    "description": (
                        "目标工具的入参；通常为对象，键=目标工具 input_schema 的字段"
                        "（如 {\"query\": \"...\"}），也接受 JSON 字符串。先用 hasn.cloud.tool.search "
                        "查目标工具 schema 再填。"
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["name"],
            "additionalProperties": True,
        }

    async def execute(
        self,
        agent_context: AgentContext,
        arguments: dict[str, Any],
    ) -> Any:
        name = str(arguments.get("name") or "").strip()
        if not name:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, "tool.call: missing 'name'")

        params = self._extract_params(arguments)

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
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f"Tool not found: {name}")

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

    def _extract_params(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """把 Runtime 传来的 params 归一化成 dict，兼容三种到达形态。

        不同 function-calling Runtime 对「内层工具入参」的承载方式不一致：
        1. 对象（正常）：``{"name": ..., "params": {"query": "..."}}``；
        2. JSON 字符串：``{"name": ..., "params": "{\"query\": \"...\"}"}``（部分 Runtime 把对象序列化成串）；
        3. 平铺顶层：``{"name": ..., "query": "..."}``（部分 Runtime 不会嵌套 params，直接铺在顶层）。

        服务端宽容接收，三种都还原成内层 params dict——避免「参数没透传到云端」。
        """
        raw = arguments.get("params")
        params: dict[str, Any] = {}
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                try:
                    decoded = json.loads(text)
                except (ValueError, TypeError):
                    raise McpToolError(
                        McpErrorCode.TOOL_NOT_FOUND, "tool.call: 'params' string is not valid JSON"
                    )
                if not isinstance(decoded, dict):
                    raise McpToolError(
                        McpErrorCode.TOOL_NOT_FOUND, "tool.call: 'params' must be an object or JSON object string"
                    )
                params = decoded
        elif isinstance(raw, dict):
            params = dict(raw)
        elif raw is not None:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, "tool.call: 'params' must be an object")

        # 形态 3 兜底：params 为空但顶层带了非保留键 → 视为被平铺的内层入参。
        if not params:
            flattened = {k: v for k, v in arguments.items() if k not in ("name", "params")}
            if flattened:
                params = flattened
        return params

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

        return {
            "ok": False,
            "error": "input_validation_failed",
            "tool": inner_tool.name,
            "missing": missing,
            "invalid": invalid,
            "input_schema": schema,
            "schema_hash": schema_hash(schema),
        }
