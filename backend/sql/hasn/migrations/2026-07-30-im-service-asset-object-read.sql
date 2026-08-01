-- IM 在发送附件时需要校验逻辑资产关联的物理对象状态。
-- 仅授予对象元数据读取权限；上传、回收与删除仍由 Owner 存储域负责。
GRANT SELECT ON public.hasn_storage_objects TO astra_im_service;
