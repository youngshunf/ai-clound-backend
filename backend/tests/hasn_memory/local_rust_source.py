"""跨仓契约钉子的公共取源helper：在同机 side-by-side 的 hasn-node 检出里定位一个 Rust 源文件。

doc19 的记忆契约横跨两仓（云端 Python + 本地 Rust），字段名 / 事件名一旦单边漂移，症状都不是
报错而是**功能静默停摆**（上行永久拒绝、合并被 422 拒在门外），排查成本极高。故凡是双端共有的
名字，都在云端测试里比对**真实 Rust 源文件**。

两条规则写死在这里，各测试文件共用一份实现（复制粘贴必然只修一处、漏另一处）：

1. **找不到本地仓就 skip，不硬失败**——云端仓的 CI 不检出 hasn-node，硬依赖会把这些断言变成
   永久红；而引入不对称的现场恰恰是开发机（两仓并排、改了一侧忘了另一侧）。各调用方还应另备
   一条显式期望集合的钉子兜底，两类断言都在、不留空档。
2. **配对 worktree 优先**——跨仓任务按约定在两仓开**同名** worktree（分支名从文档号确定性派
   生）。本仓在 worktree 里跑时，比对对象必须是**同名的** hasn-node worktree，而不是主 clone：
   否则跨仓改动做在两仓 worktree 里、这条测试却拿主 clone 的旧源文件去比——一路绿灯，等合并回
   主分支才炸，正好错过它该守住的那一刻（前序切片踩过这个坑）。
"""

from __future__ import annotations

from pathlib import Path

#: 本文件所在目录（`backend/tests/hasn_memory/`）——上溯找兄弟目录 `hasn-node` 的起点。
_HERE = Path(__file__).resolve()


def paired_worktree_name() -> str | None:
    """本测试文件所在的 worktree 名（不在 worktree 里则 ``None``）。"""
    parts = _HERE.parts
    if '.worktrees' not in parts:
        return None
    index = parts.index('.worktrees')
    return parts[index + 1] if index + 1 < len(parts) else None


def find_local_rust_source(relative: Path | str) -> Path | None:
    """在同机 hasn-node 检出里找 ``relative``（相对仓库根）；找不到返回 ``None``。

    候选顺序：**同名 worktree** → 主 clone → 其余 worktree（按名字排序，纯兜底）。
    """
    relative = Path(relative)
    # 本仓可能是主 clone（<project>/hasn-cloud-backend）也可能是 worktree
    # （<project>/hasn-cloud-backend/.worktrees/<name>），故逐级上溯找兄弟目录 hasn-node。
    for ancestor in _HERE.parents:
        node_repo = ancestor / 'hasn-node'
        if not node_repo.is_dir():
            continue
        worktrees = node_repo / '.worktrees'
        candidates: list[Path] = []
        paired = paired_worktree_name()
        if paired and worktrees.is_dir():
            candidates.append(worktrees / paired / relative)
        candidates.append(node_repo / relative)
        if worktrees.is_dir():
            candidates.extend(sorted(path / relative for path in worktrees.iterdir() if path.is_dir()))
        return next((path for path in candidates if path.is_file()), None)
    return None
