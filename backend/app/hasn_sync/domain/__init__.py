"""hasn_sync.domain · 纯同步逻辑（revision 单调、幂等键、retention 判定）

**负面约束**：只放纯逻辑，无 IO；且**不 import 任何业务模块类型**（§0.1）。
"""
