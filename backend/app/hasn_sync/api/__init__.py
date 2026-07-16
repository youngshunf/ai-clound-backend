"""hasn_sync.api · 同步下行协议层（cursor 拉取、full-refresh 端点）

薄协议层：把 owner 的 revision 游标翻译成事件页返回给 daemon 的 SyncPullLoop（D 线）。
"""
