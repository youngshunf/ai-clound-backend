"""素材站目录与通用素材站工具（hasn_stock，模块 A-P2）。

平台级素材站接入：`hasn_stock_providers` 后台可配目录（provider/key/媒体类型/下载域/开关/failover 顺序）
+ `StockService`（provider 适配器 + 默认 failover 链）。分身经云端 platform 工具 `hasn.stock.search` /
`hasn.stock.download`（`backend/app/mcp/tools/stock.py`）触达，全分身零前置可用，不依赖任何本地引擎。

独立 PG schema `hasn_stock`（ADR-15 应用命名空间隔离）。
设计事实源：docs/Agent产物系统/01-分身资源检索与素材站工具设计.md §4.5–§4.6。
"""
