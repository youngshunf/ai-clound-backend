# 记忆子系统模型（独立 PG schema hasn_memory）。
# owner_memory 线（USER.md 合并，标准 fba 结构）+ namespace_revision + 四主体四层设计表族
# （镜像本地 crate，非标准结构，纯元数据登记）。导入即注册进 MappedBase.metadata。

# owner_memory 线（USER.md 合并，ADR 2026-05-30）
from backend.app.hasn_memory.model.owner_memory import HasnOwnerMemory as HasnOwnerMemory
from backend.app.hasn_memory.model.owner_memory import HasnOwnerMemoryContribution as HasnOwnerMemoryContribution

# namespace_revision（同步命名空间权威 revision，原 public.memory_namespace_revisions）
from backend.app.hasn_memory.model.namespace_revision import MemoryNamespaceRevision as MemoryNamespaceRevision

# 四主体四层设计表族（doc 04，镜像本地 crate）
from backend.app.hasn_memory.model.episodic_turn import EpisodicTurn as EpisodicTurn
from backend.app.hasn_memory.model.semantic_fact import SemanticFact as SemanticFact
from backend.app.hasn_memory.model.memory_event import MemoryEvent as MemoryEvent
from backend.app.hasn_memory.model.extraction_job import ExtractionJob as ExtractionJob
