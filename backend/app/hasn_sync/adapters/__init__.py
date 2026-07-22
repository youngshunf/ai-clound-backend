"""hasn_sync.adapters · 同步内核持久化（事件流/cursor/inbox 的 SQLAlchemy 访问）

用 `astra_sync_service` 角色的 session maker（§3.2）。**不外键关联业务 schema**——事件流独立
存储，只认 owner_id + revision + payload。
"""
