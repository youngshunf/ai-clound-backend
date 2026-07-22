"""hasn_sync.application · 同步内核编排（SyncAppender 实现、full-refresh 协议）

`SyncAppender` port 的实现落在这里，唯一去调 `hasn_sync.append_event(...)` 单实现（§3.2）。
"""
