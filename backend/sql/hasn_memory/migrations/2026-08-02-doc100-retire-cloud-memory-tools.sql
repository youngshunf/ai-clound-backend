-- doc100：云端记忆工具与 owner contribution 单独写入通道退役。
--
-- 旧云端工具写入的事实没有真实 node_id；保留事实本身供主人追溯与主脑整理，
-- 但把来源明确标成 retired，避免任何 hasn-node 将它误认成本节点自产片。
UPDATE hasn_memory.semantic_fact
SET
    origin_kind = 'retired',
    origin_node_id = 'legacy-cloud'
WHERE origin_kind = 'node'
  AND origin_node_id IS NULL;

-- contribution 旧通道不再有写者或读者；本地语义事实是唯一写入权威。
DROP TABLE IF EXISTS hasn_memory.owner_memory_contribution;
