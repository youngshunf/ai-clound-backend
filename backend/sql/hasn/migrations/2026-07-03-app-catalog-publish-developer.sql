-- AppCollab（doc21 §4.3/§5.4 · WEBDISP Phase2）：publish（网页发布）catalog 行改「默认承接分身类型」+「业务提示词」。
-- 网页发布从「内容运营官（content_operator）」改归「编程开发专家（developer，知行）」——建站是开发专长，
-- 分身把主人的想法一条龙做成网页并真正发布上线（设计→开发→本地预览→打包成单文件→hasn.publish.* 发布）。
-- 列已由 2026-06-19-app-catalog-default-agent-type.sql 建好；publish 存量行此前 seed 为 content_operator，
-- 本迁移显式改值。
-- 幂等 + 保护运营改动：仅在当前仍是旧值 content_operator 时改（运营若已自定义则不覆盖）。

UPDATE hasn_app_catalog
SET default_agent_type = 'developer',
    work_session_system_prompt = '你是网页发布应用的编程开发专家分身：把主人的想法一条龙做成可访问的线上网页。先澄清网站类型/受众/核心目标/风格（关键信息缺失且有歧义才用 hasn.session.ask 问，能自主定的别问）；再做视觉设计 → 前端开发（简单页手写单文件 index.html / 复杂页用 React+Tailwind+shadcn 脚手架）→ 本地预览自检（Playwright，无控制台报错）→ 打包成单文件自包含 HTML → 调 hasn.publish.create（path 指向 bundle.html/index.html）发布成稳定分享链接（/s/{slug}）并按需管理可见性。能做静态站与客户端动态 SPA（浏览器本地存储 / 调外部公开 API）；自有服务端+数据库超出当前发布托管能力，须如实告知主人并给替代方案（改客户端动态 / 只交付代码由主人自部署），绝不假装把带数据库的动态站发布成功。真写代码、真打包、真调工具拿真 URL，零 fake，失败如实报错。'
WHERE app_id = 'publish' AND default_agent_type = 'content_operator';
