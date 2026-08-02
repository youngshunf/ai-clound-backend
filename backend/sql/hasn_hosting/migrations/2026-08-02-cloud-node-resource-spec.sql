-- =====================================================
-- 托管状态表补资源档位列（设计 §13 H9-b）
--
-- 背景：主人在云端节点的 WebUI 装了图坊/语音引擎后内存需求跳一档，需要 resize。云端要能
-- 回答两个问题才能把这件事做完整：「这个节点现在什么规格」（UI 如实展示 + 判断该不该调）
-- 与「它的数据卷占了多少」（磁盘配额落地前，这是唯一能看见的量）。
--
-- 三列都是**镜像**，权威在 hosting-agent（它从 docker inspect / du 回读真实生效值）。
-- 云端存一份是为了列表页不必逐节点打宿主；两者不一致时以 hosting-agent 为准并回写。
--
-- 列语义（注意 disk_used_mb 的 NULL 不是 0）：
--   memory_mb    0    = 尚未从宿主回报过（不是「限 0 字节」）
--   cpus         0    = 同上
--   disk_used_mb NULL = **测不出来**（例如 Docker Desktop 把卷放在虚拟机内，宿主看不到路径）；
--                       写 0 会被读成「这个节点没占空间」，是最坏的一种误导，故必须可空
--
-- 幂等：`ADD COLUMN IF NOT EXISTS`，重跑无副作用。
--
-- 事实源：docs/hasn-node设计文档/云端节点托管/00-无头hasn-node托管总体设计.md §13 H9-b
-- =====================================================

ALTER TABLE "public"."hasn_cloud_nodes"
  ADD COLUMN IF NOT EXISTS "memory_mb"    integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "cpus"         double precision NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS "disk_used_mb" integer;

COMMENT ON COLUMN "public"."hasn_cloud_nodes"."memory_mb"
  IS '单节点内存上限 MiB（0=尚未从宿主回报）';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."cpus"
  IS '单节点 CPU 配额（核数，0=尚未从宿主回报）';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."disk_used_mb"
  IS '数据卷实际占用 MiB；NULL=测不出来（不是 0，0 会被读成没占空间）';

-- 复核
DO $$
DECLARE
  v_cols int;
BEGIN
  SELECT count(*) INTO v_cols
    FROM information_schema.columns
   WHERE table_schema = 'public'
     AND table_name = 'hasn_cloud_nodes'
     AND column_name IN ('memory_mb', 'cpus', 'disk_used_mb');
  RAISE NOTICE '[改后] hasn_cloud_nodes 资源档位列 % 个（应为 3）', v_cols;
  IF v_cols <> 3 THEN
    RAISE EXCEPTION '资源档位列没有全部建出来（实际 % 个）', v_cols;
  END IF;
END $$;
