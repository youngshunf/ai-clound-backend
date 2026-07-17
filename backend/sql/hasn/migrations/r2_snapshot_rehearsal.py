#!/usr/bin/env python3
"""R2-11 / R2-12 · migration 快照副本全链演练（云端 IM 服务化重构，doc92 §4 R2-11/R2-12 + §9 门禁）。

在**本地快照副本**（`pg_dump` 本地 dev 库 → 恢复到临时本地库）上，正反两向各跑通一次 R2 cutover
migration 族，并在 cutover 后跑「三角色权限负测」，作为 R2 出闸的两条本地门禁：

- **恢复/回滚演练（正反两向·本地副本）**（§9 R2 行「必须」）：forward 全套 → reverse 结构级回滚，各一次全绿。
- **权限负测（三角色）**（§9 R2 行「本地快照副本」）：cutover 后跑 `r2-11-permission-negative-test.sql`，
  钉死 §3.2 三个服务角色（`astra_im_service` / `astra_sync_service` / `astra_python_backend`）的
  DML/读/EXECUTE 边界；旧 role 直写 `hasn_im`/`hasn_sync` 必失败、仅 EXECUTE `append_event` 放行。

**铁律安全边界**（§4 R2-11 头 + 福仔「不要跟生产环境有任何关系」）：
- 全程只操作**临时快照库** `huanxing_r2_rehearsal`，**绝不**对 dev 主库 `huanxing` 跑任何 migration；
  dev 主库仅被**只读** `pg_dump` 一次。演练完 DROP 掉快照库，dev 主库零改动。
- **不触生产**：本演练纯本地（PG:15432），零生产依赖（§9「本地/生产边界」）。
- 用超级用户 `mac`（brew PG 本地 trust）建/删临时库、SET ROLE 到 astra_* 判权。

用法：`python backend/sql/hasn/migrations/r2_snapshot_rehearsal.py`
证据：`test-results/r2-snapshot-rehearsal.json`（gitignored）+ 控制台摘要。退出码 0=全绿。
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

# ── 连接与库名（本地 brew PG）───────────────────────────────────────────────
HOST = "localhost"
PORT = "15432"
SUPERUSER = "mac"  # brew PG 本地超级用户（trust 认证，无密码）
SRC_DB = "huanxing"  # dev 主库——只读 pg_dump，绝不跑 migration
SNAP_DB = "huanxing_r2_rehearsal"  # 临时快照库——演练全在这里，完事 DROP

MIG_DIR = Path(__file__).resolve().parent
REPO_ROOT = MIG_DIR.parents[3]  # backend/sql/hasn/migrations → 仓根
EVIDENCE_PATH = REPO_ROOT / "test-results" / "r2-snapshot-rehearsal.json"

# ── R2 cutover migration 族（doc92 §4 R2-11「Files to run in order」）─────────
# R2-01/06/08/09/10/12/13/14 无 SQL 迁移文件（代码/pytest 卡），只有以下有 DDL。
FORWARD_MIGRATIONS = [
    "2026-07-16-r2-02-conversation-seq.sql",
    "2026-07-16-r2-03-conversation-memberships.sql",
    "2026-07-16-r2-04-integration-events.sql",
    "2026-07-16-r2-05-event-consumers.sql",
    "2026-07-16-r2-07-hasn-sync-append-event.sql",
    "2026-07-16-r2-11-schema-cutover.sql",  # ← THE CUTOVER（仅快照副本）
]
PERM_NEG_TEST = "2026-07-16-r2-11-permission-negative-test.sql"
REVERSE_MIGRATION = "2026-07-16-r2-11-schema-cutover.reverse.sql"

# ── 铁律安全断言：绝不把快照库指向 dev 主库 ─────────────────────────────────
assert SNAP_DB != SRC_DB, "快照库不得等于 dev 主库"
assert SNAP_DB.endswith("_rehearsal"), "快照库名必须以 _rehearsal 收尾（防误指真库）"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def psql(db: str, *, sql: str | None = None, file: str | None = None, on_error_stop: bool = True) -> subprocess.CompletedProcess[str]:
    """对指定库跑 SQL。on_error_stop 时任一 SQL 错误 → 退出码非零。"""
    cmd = ["psql", "-h", HOST, "-p", PORT, "-U", SUPERUSER, "-d", db, "-X", "-q"]
    if on_error_stop:
        cmd += ["-v", "ON_ERROR_STOP=1"]
    if file is not None:
        cmd += ["-f", file]
    elif sql is not None:
        cmd += ["-c", sql]
    return _run(cmd)


def psql_scalar(db: str, sql: str) -> str:
    proc = psql_tuples(db, sql)
    return proc.stdout.strip()


def psql_tuples(db: str, sql: str) -> subprocess.CompletedProcess[str]:
    cmd = ["psql", "-h", HOST, "-p", PORT, "-U", SUPERUSER, "-d", db, "-X", "-q", "-A", "-t", "-c", sql]
    return _run(cmd)


def drop_snapshot_db() -> None:
    """终止快照库连接并 DROP（幂等）。只对快照库操作。"""
    psql_tuples(
        "postgres",
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{SNAP_DB}' AND pid <> pg_backend_pid();",
    )
    psql("postgres", sql=f'DROP DATABASE IF EXISTS "{SNAP_DB}";', on_error_stop=True)


def main() -> int:
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.monotonic()
    ev: dict = {
        "rehearsal": "r2_snapshot_cutover",
        "design": "docs/hasn-node设计文档/01-核心架构/实施/92-云端IM-Python底层服务化重构实施方案.md (§4 R2-11/R2-12 + §9)",
        "started_at": started,
        "src_db": SRC_DB,
        "snapshot_db": SNAP_DB,
        "safety": "全程只操作临时快照库；dev 主库仅只读 pg_dump 一次；完事 DROP 快照库；零生产依赖。",
        "steps": [],
    }
    failures: list[str] = []

    def step(name: str, ok: bool, detail: str = "", dt: float = 0.0) -> None:
        ev["steps"].append({"name": name, "ok": ok, "detail": detail, "seconds": round(dt, 2)})
        status = "OK" if ok else "FAIL"
        print(f"[r2-rehearsal] {status}: {name}{(' — ' + detail) if detail else ''} ({dt:.1f}s)", flush=True)
        if not ok:
            failures.append(f"{name}: {detail}")

    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 0) 预检：超级用户可达、dev 主库存在、快照库名安全
    ts = time.monotonic()
    who = psql_scalar("postgres", "SELECT current_user || '|' || (SELECT rolsuper::text FROM pg_roles WHERE rolname = current_user);")
    ok = who.startswith(f"{SUPERUSER}|t")
    step("预检-超级用户可达", ok, f"current_user={who}", time.monotonic() - ts)
    if not ok:
        return _finish(ev, failures, t0)

    try:
        # 1) 建快照副本：DROP 旧 → CREATE → pg_dump 主库 | psql 恢复（dev 主库只读）
        ts = time.monotonic()
        drop_snapshot_db()
        cr = psql("postgres", sql=f'CREATE DATABASE "{SNAP_DB}" TEMPLATE template0;', on_error_stop=True)
        if cr.returncode != 0:
            step("建快照库", False, cr.stderr.strip()[:400], time.monotonic() - ts)
            return _finish(ev, failures, t0)
        # pg_dump dev 主库（只读）→ psql 恢复到快照库。--no-owner/--no-privileges 避免 role 依赖。
        dump = subprocess.Popen(
            ["pg_dump", "-h", HOST, "-p", PORT, "-U", SUPERUSER, "-d", SRC_DB, "--no-owner", "--no-privileges"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        restore = subprocess.Popen(
            ["psql", "-h", HOST, "-p", PORT, "-U", SUPERUSER, "-d", SNAP_DB, "-X", "-q"],
            stdin=dump.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if dump.stdout:
            dump.stdout.close()
        r_out, r_err = restore.communicate()
        dump.wait()
        # 校验克隆保真：快照库业务行数 = 主库
        src_msg = psql_scalar(SRC_DB, "SELECT count(*) FROM public.hasn_messages;")
        snap_msg = psql_scalar(SNAP_DB, "SELECT count(*) FROM public.hasn_messages;")
        clone_ok = src_msg == snap_msg and src_msg.isdigit() and int(src_msg) > 0
        step("克隆快照副本(pg_dump 主库→恢复)", clone_ok, f"messages 主库={src_msg} 快照={snap_msg}", time.monotonic() - ts)
        if not clone_ok:
            return _finish(ev, failures, t0)

        # 2) 正向 migration 族（在快照库上按序全套）
        for fname in FORWARD_MIGRATIONS:
            ts = time.monotonic()
            proc = psql(SNAP_DB, file=str(MIG_DIR / fname))
            step(f"正向: {fname}", proc.returncode == 0, proc.stderr.strip()[:400], time.monotonic() - ts)

        # 3) cutover 后结构断言：hasn_im/hasn_sync schema 与迁移后表位
        ts = time.monotonic()
        checks = {
            "hasn_im schema 存在": "SELECT count(*) FROM pg_namespace WHERE nspname='hasn_im';",
            "hasn_im.hasn_messages 存在": "SELECT count(*) FROM pg_tables WHERE schemaname='hasn_im' AND tablename='hasn_messages';",
            "hasn_im.integration_events 存在(去前缀)": "SELECT count(*) FROM pg_tables WHERE schemaname='hasn_im' AND tablename='integration_events';",
            "hasn_im.event_consumer_offsets 存在": "SELECT count(*) FROM pg_tables WHERE schemaname='hasn_im' AND tablename='event_consumer_offsets';",
            "hasn_sync.hasn_sync_events 存在": "SELECT count(*) FROM pg_tables WHERE schemaname='hasn_sync' AND tablename='hasn_sync_events';",
            "三角色齐备": "SELECT count(*) FROM pg_roles WHERE rolname IN ('astra_im_service','astra_sync_service','astra_python_backend');",
            "public.hasn_messages 已移走": "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='hasn_messages';",
        }
        expect = {
            "hasn_im schema 存在": "1", "hasn_im.hasn_messages 存在": "1",
            "hasn_im.integration_events 存在(去前缀)": "1", "hasn_im.event_consumer_offsets 存在": "1",
            "hasn_sync.hasn_sync_events 存在": "1", "三角色齐备": "3", "public.hasn_messages 已移走": "0",
        }
        cut_detail = []
        cut_ok = True
        for label, q in checks.items():
            got = psql_scalar(SNAP_DB, q)
            good = got == expect[label]
            cut_ok = cut_ok and good
            cut_detail.append(f"{label}={got}{'' if good else f'(期望{expect[label]})'}")
        step("cutover 后结构断言", cut_ok, "; ".join(cut_detail), time.monotonic() - ts)

        # 4) 权限负测（三角色·§3.2 边界）——RAISE EXCEPTION 即硬失败
        ts = time.monotonic()
        proc = psql(SNAP_DB, file=str(MIG_DIR / PERM_NEG_TEST))
        # 负测通过时会 RAISE NOTICE 总结行；失败 RAISE EXCEPTION → 退出码非零
        passed_line = "R2-12 权限负测全部通过" in (proc.stdout + proc.stderr)
        step("权限负测(三角色·旧role写hasn_im/hasn_sync必拒)", proc.returncode == 0 and passed_line,
             (proc.stderr.strip()[:400] or "三角色边界断言全过"), time.monotonic() - ts)

        # 5) 反向 migration（结构级回滚演练）
        ts = time.monotonic()
        proc = psql(SNAP_DB, file=str(MIG_DIR / REVERSE_MIGRATION))
        step(f"反向: {REVERSE_MIGRATION}", proc.returncode == 0, proc.stderr.strip()[:400], time.monotonic() - ts)

        # 6) 反向后结构断言：表移回 public，hasn_im schema 与三角色已 drop
        ts = time.monotonic()
        rev_checks = {
            "public.hasn_messages 已移回": ("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='hasn_messages';", "1"),
            "public.hasn_im_integration_events 前缀已恢复": ("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='hasn_im_integration_events';", "1"),
            "hasn_im schema 已 drop": ("SELECT count(*) FROM pg_namespace WHERE nspname='hasn_im';", "0"),
            "三角色已 drop": ("SELECT count(*) FROM pg_roles WHERE rolname IN ('astra_im_service','astra_sync_service','astra_python_backend');", "0"),
        }
        rev_detail = []
        rev_ok = True
        for label, (q, exp) in rev_checks.items():
            got = psql_scalar(SNAP_DB, q)
            good = got == exp
            rev_ok = rev_ok and good
            rev_detail.append(f"{label}={got}{'' if good else f'(期望{exp})'}")
        step("反向后结构断言(表回public/schema+role已drop)", rev_ok, "; ".join(rev_detail), time.monotonic() - ts)

    finally:
        # 7) 清理：DROP 快照库（dev 主库自始至终零改动）
        ts = time.monotonic()
        try:
            drop_snapshot_db()
            step("清理-DROP 快照库", True, f"{SNAP_DB} dropped", time.monotonic() - ts)
        except Exception as exc:  # noqa: BLE001
            step("清理-DROP 快照库", False, repr(exc), time.monotonic() - ts)

    return _finish(ev, failures, t0)


def _finish(ev: dict, failures: list[str], t0: float) -> int:
    ev["status"] = "passed" if not failures else "failed"
    ev["failures"] = failures
    ev["total_seconds"] = round(time.monotonic() - t0, 2)
    ev["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    EVIDENCE_PATH.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        print(f"\n[r2-rehearsal] FAILED（{len(failures)} 项）：", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"\n[r2-rehearsal] ✓ R2-11/R2-12 快照全链演练全绿（正反两向 + 三角色权限负测）；证据 {EVIDENCE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
