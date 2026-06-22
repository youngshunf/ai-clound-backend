"""金融数据（hasn_finance，app_id=finance）—— 行情/投研只读数据 AI-Native 应用。

cloud-brokered：分身经云端 MCP（gateway_internal）/ owner 经云端 read-API 取数，云端 finance_provider
中转到独立部署的 finance-data-service（唯一接触 akshare 的地方）。主云端不装 akshare。

设计：docs/hasn-node设计文档/14-AI-Native应用平台/24-金融数据源(akshare)行情与投研应用接入设计.md
"""
