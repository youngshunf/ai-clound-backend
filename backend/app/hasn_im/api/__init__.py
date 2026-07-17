"""hasn_im.api · HTTP 协议层（薄）

只做：入参校验 → 构造命令 + ServicePrincipal（从鉴权上下文取 sender/origin_node，不信 body）→
调 ports → 统一信封返回。**不**在协议层写业务判定/事务/判权（R1-09 协议层净化）。
"""
