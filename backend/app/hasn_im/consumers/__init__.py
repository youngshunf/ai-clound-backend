"""hasn_im.consumers · integration_events 消费者（§7.2）

两类消费者：
- **durable**（sync_projector / audit_projector）：事实源投影，必须 at-least-once、有进度游标、
  失败重试到成功或死信；
- **best-effort**（realtime_notifier / push_notifier）：在线帧/推送，可丢可重复，客户端去重（§7.4）。

统一走 §6.1 的 relay 框架消费，禁止各业务各抄一份轮询。
"""
