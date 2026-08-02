# 记忆子系统模型（独立 PG schema hasn_memory）。
# owner_memory 线（USER.md 合并，标准 fba 结构）+ namespace_revision + 四主体四层设计表族
# （镜像本地 crate，非标准结构，纯元数据登记）。导入即注册进 MappedBase.metadata。

# owner_memory 线（USER.md 合并，ADR 2026-05-30）
# 四主体四层设计表族（doc 04，镜像本地 crate）
# 注：extraction_job 表随 doc18 退役自动提取管线一并删除（见 2026-07-14-drop-memory-extraction-pipeline.sql）
from backend.app.hasn_memory.model.episodic_turn import EpisodicTurn as EpisodicTurn

# doc19 多节点记忆分层：purge 墓碑 + 合并轮次 + 合并待办
from backend.app.hasn_memory.model.fact_tombstone import FactTombstone as FactTombstone
from backend.app.hasn_memory.model.memory_event import MemoryEvent as MemoryEvent
from backend.app.hasn_memory.model.merge_request import MergeRequest as MergeRequest
from backend.app.hasn_memory.model.merge_run import MergeRun as MergeRun

# namespace_revision（同步命名空间权威 revision，原 public.memory_namespace_revisions）
from backend.app.hasn_memory.model.namespace_revision import MemoryNamespaceRevision as MemoryNamespaceRevision
from backend.app.hasn_memory.model.owner_memory import HasnOwnerMemory as HasnOwnerMemory
from backend.app.hasn_memory.model.owner_profile_coverage import OwnerProfileCoverage as OwnerProfileCoverage
from backend.app.hasn_memory.model.peer_portrait import PeerPortrait as PeerPortrait
from backend.app.hasn_memory.model.semantic_fact import SemanticFact as SemanticFact
