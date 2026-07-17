"""hasn_im.application · 应用编排（ImGateway/RelationGateway 实现、事务边界）

port 的实现落在这里，编排 domain 规则 + adapters 持久化 + 单事务内的 message/grant/
integration_event。对外经 ports 暴露，**不**被其他业务模块直接 import。
"""
