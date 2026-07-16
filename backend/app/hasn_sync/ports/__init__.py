"""hasn_sync.ports · 对外契约（唯一允许被其他模块 import 的层）"""

from backend.app.hasn_sync.ports.dto import SyncEnvelope, SyncEventRef
from backend.app.hasn_sync.ports.sync_appender import SyncAppender

__all__ = ['SyncAppender', 'SyncEnvelope', 'SyncEventRef']
