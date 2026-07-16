"""hasn_im.domain · 纯领域逻辑（判权规则、seq/epoch 不变量、值对象）

**负面约束（R1-01）**：只放**纯逻辑**（无 IO、无 Session、无 Redis）；**不得**建与
SQLAlchemy model 平行的实体层——ORM 行仍是唯一持久化实体，domain 只承载规则与不变量。
"""
