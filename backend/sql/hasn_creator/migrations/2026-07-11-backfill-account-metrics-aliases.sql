-- Migration: 回填历史误落 metrics_json 的账号指标到规范列
-- Date: 2026-07-11
-- Description:
--   分身抓取回填指标时曾把平台特有口径写成自然 key（如小红书主页「获赞与收藏」的合并数字
--   `xiaohongshu_total_likes_and_favorites`、各种 likes/posts 变体），而 update_account_metrics
--   早期无别名映射，未知 key 被静默塞进 metrics_json，专用列 total_likes/total_favorites/total_posts
--   保持默认 0，页面读列显示 0（假成功）。服务层已补别名归一（_METRIC_KEY_ALIASES）；本迁移把存量
--   metrics_json 里的历史口径回填到对应规范列。
--   幂等：仅当目标列为 NULL/0 且 metrics_json 里存在数字型别名值时回填；重复执行不改变结果。
--   注意：`scraped_posts_count`（本次抓取数、通常 has_more=true 的部分值）不是账号笔记总数，
--   故意不映射到 total_posts，保留在 metrics_json 作抓取元数据。

DO $$
DECLARE
    -- 规范列 → 该列的历史别名 key 列表（顺序即优先级，前者命中后后者自动跳过）。
    mapping jsonb := '{
        "total_likes": ["xiaohongshu_total_likes_and_favorites","total_likes_and_favorites","likes_and_favorites","likes_favorites","likes","like_count","total_like","liked"],
        "total_favorites": ["favorites","favorite_count","collects","collect_count","collections","collection_count","saves","saved"],
        "total_comments": ["comments","comment_count"],
        "total_posts": ["posts","post_count","posts_count","works","works_count","notes","note_count","notes_count","total_notes","videos_count","video_count"],
        "followers": ["fans","fans_count","follower_count","followers_count"],
        "following": ["follows","follow_count","following_count"]
    }'::jsonb;
    col  text;
    keys jsonb;
    k    text;
BEGIN
    FOR col, keys IN SELECT * FROM jsonb_each(mapping) LOOP
        FOR k IN SELECT jsonb_array_elements_text(keys) LOOP
            EXECUTE format(
                'UPDATE hasn_creator.account
                    SET %1$I = (metrics_json->>%2$L)::int
                  WHERE (%1$I IS NULL OR %1$I = 0)
                    AND metrics_json ? %2$L
                    AND (metrics_json->>%2$L) ~ ''^-?[0-9]+$''',
                col, k
            );
        END LOOP;
    END LOOP;
END $$;
