"""hasn_im.observability · 云端 IM 服务化可观测性（R2-14·doc16 §12.2）。

导出 §12.2 指标全集与初始门槛常量。指标注册进 prometheus_client 默认 REGISTRY，经既有
`/metrics` ASGI 端点导出（与 `backend/common/observability/prometheus.py` 同一出口）。
"""

from backend.app.hasn_im.observability import metrics

__all__ = ['metrics']
