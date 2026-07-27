-- R3：把任务事件的节点可见性固化到 sync 自有数据，禁止 sync 角色跨域读取 hasn_task.assignment。
--
-- 新写点会直接携带 visible_node_ids；本迁移仅修复切换前的存量事件。优先采用当前权威
-- assignment，没有 assignment 时回退事件原有 executor_node_id/node_id。空数组表示
-- 没有可投递节点；内置任务仍由 pull 端按 created_by_kind='builtin' 对主人所有节点广播。

BEGIN;

WITH task_event_visibility AS (
    SELECT
        e.event_id,
        COALESCE(
            (
                SELECT jsonb_agg(DISTINCT a.executor_node_id)
                FROM hasn_task.assignment AS a
                WHERE a.owner_id = e.owner_id
                  AND a.task_uuid = COALESCE(
                      e.payload->>'task_uuid',
                      e.payload->>'task_id',
                      e.aggregate_id
                  )
                  AND a.assignment_state = 'assigned'
                  AND NULLIF(a.executor_node_id, '') IS NOT NULL
            ),
            CASE
                WHEN COALESCE(
                    NULLIF(e.payload->>'executor_node_id', ''),
                    NULLIF(e.payload->>'node_id', '')
                ) IS NOT NULL
                THEN jsonb_build_array(
                    COALESCE(
                        NULLIF(e.payload->>'executor_node_id', ''),
                        NULLIF(e.payload->>'node_id', '')
                    )
                )
                ELSE '[]'::jsonb
            END
        ) AS visible_node_ids
    FROM hasn_sync.hasn_sync_events AS e
    WHERE e.event_type LIKE 'task.%'
      AND e.event_type <> 'task_run.summary_reported'
      AND NOT (e.payload ? 'visible_node_ids')
)
UPDATE hasn_sync.hasn_sync_events AS e
SET payload = jsonb_set(
    COALESCE(e.payload, '{}'::jsonb),
    '{visible_node_ids}',
    visibility.visible_node_ids,
    true
)
FROM task_event_visibility AS visibility
WHERE e.event_id = visibility.event_id;

COMMIT;
