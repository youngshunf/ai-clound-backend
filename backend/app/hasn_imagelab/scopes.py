"""imagelab（图坊，自研本地图像处理引擎，模块 14 doc30）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/30-图像处理AI-Native应用(自研引擎·图坊)架构设计.md §5.4/§5.5；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

落地真相（hasn-node `crates/hasn-mcp/src/imagelab.rs`，P3 待落；本表是云端侧契约源，
imagelab.rs `capability_scopes()` 须与之对齐——同 film/reel 的跨仓零漂移守卫）：
- 读类（analyze/job.get/job.list）统一 `imagelab:read`（出厂 Allow，只读/分析无副作用）；
- 非破坏性处理类（process/pipeline/animate/enhance/recipe.save/list/get/import）统一 `imagelab:process`
  （出厂 Allow——默认不覆盖原图、产物只落本地、可回滚）；
- 写盘导出类（export，写本地输出目录 + 登记产物）`imagelab:export`（出厂 Allow——写盘动作，非读，不挂 read）；
- 大批量类（batch，配方批量应用到 N 图/目录）`imagelab:batch`（出厂 Ask——耗算力/可能计费）；
- 破坏性类（retouch=inpaint/水印去除/物体消除，伪造/抹除像素）`imagelab:destructive`（出厂 Ask）；
- 生成类（generate，桥接平台 hasn.image.generate 花积分）`imagelab:generate`（出厂 Ask）；
- 外发分享类（share，产物上云发好友/群）`imagelab:share`（出厂 Ask——外发上云）。

注：图坊生成能力不自建，桥接平台 hasn.image.generate（new-api）；本应用只铸自身 imagelab:* 域 scope。
"""

from __future__ import annotations

IMAGELAB_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'imagelab:read': {
        'label_zh': '读取与分析图片',
        'label_en': 'Read and analyze images',
        'domain': 'imagelab',
        'risk': 'low',
        'description': '以 Agent 身份读图片元信息/尺寸/格式/直方图/主色、检测主体/文字区域、'
        '看图说话、查批处理任务（只读无副作用）',
        'description_en': 'Read image metadata, dimensions, format, histogram, and dominant colors; detect subjects and text regions; caption images; and check batch jobs as the agent (read-only, no side effects)',
    },
    'imagelab:process': {
        'label_zh': '处理图片',
        'label_en': 'Process images',
        'domain': 'imagelab',
        'risk': 'low',
        'description': '非破坏性处理图片（去背景/裁剪/缩放/调色/滤镜/格式/压缩/拼图/水印/动画/本地增强）、'
        '配方编排、导入图片（默认不覆盖原图、只落本地）',
        'description_en': 'Non-destructive image processing (background removal, crop, resize, color, filters, format, compression, collage, watermark, animation, local enhancement), recipe orchestration, and importing images (never overwrites originals; stays local)',
    },
    'imagelab:batch': {
        'label_zh': '批量处理图片',
        'label_en': 'Batch-process images',
        'domain': 'imagelab',
        'risk': 'medium',
        'default_mode': 'allow',
        'description': '把一条配方批量应用到 N 张图/目录/asset 列表（处理自己图片，2026-07-05 放开出厂 Allow，主人可改 Ask）',
        'description_en': 'Apply one recipe in batch to N images, a directory, or an asset list (compute-heavy, may incur charges; confirmation required by default)',
    },
    'imagelab:destructive': {
        'label_zh': '局部消除 / 去水印',
        'label_en': 'Inpaint / remove watermark',
        'domain': 'imagelab',
        'risk': 'medium',
        'default_mode': 'allow',
        'description': '破坏性局部编辑：物体消除/路人消除/水印去除（inpaint，编辑自己图片非外发/动钱，2026-07-05 放开出厂 Allow，主人可改 Ask）',
        'description_en': 'Destructive local edits: object removal, bystander removal, watermark removal (inpaint fabricates or erases pixels; owner confirmation required by default)',
    },
    'imagelab:generate': {
        'label_zh': '生成式处理图片',
        'label_en': 'Generative image editing',
        'domain': 'imagelab',
        'risk': 'medium',
        'default_mode': 'allow',
        'description': '生成式填充/扩图/图生图/云增强，桥接平台 hasn.image.generate（生成类，2026-07-05 放开出厂 Allow，主人可改 Ask 把关积分）',
        'description_en': 'Generative fill, outpainting, image-to-image, and cloud enhancement via the platform hasn.image.generate (consumes owner credits; confirmation required by default)',
    },
    'imagelab:export': {
        'label_zh': '导出到本地目录',
        'label_en': 'Export to local directory',
        'domain': 'imagelab',
        'risk': 'low',
        'description': '把处理结果写到用户本地输出目录并登记产物（写盘动作、纯本地不上云；产物默认本地优先）',
        'description_en': "Write processed results to the user's local output directory and register the artifact (disk write, stays local; artifacts are local-first by default)",
    },
    'imagelab:share': {
        'label_zh': '分享产物到好友/群',
        'label_en': 'Share artifacts to contacts/groups',
        'domain': 'imagelab',
        'risk': 'medium',
        'default_mode': 'ask',
        'description': '把本地产物上传云端私有桶（hasn://asset）并发给指定好友/群（外发上云，默认需主人确认）',
        'description_en': 'Upload a local artifact to a cloud private bucket (hasn://asset) and send it to a chosen contact or group (outbound to cloud; owner confirmation required by default)',
    },
}
