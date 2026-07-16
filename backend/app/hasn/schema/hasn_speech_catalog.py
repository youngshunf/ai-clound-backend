"""通用语音模型签名目录 schema（云端权威·哑存储·单行下发）SPCAT-4。

- 节点下发出参 ``SpeechCatalogNodeResponse``：daemon 拉取，据 revision 比对决定重拉；
  ``catalog_json`` 是签名 catalog 逐字节原文（可空——未发布时为 None，daemon 保持「未装配」）。
- 发布出参 ``SpeechCatalogPublishResponse``：CI 发布后回显（含摘要 + 上传的公开 https 直链）。
"""

from datetime import datetime

from pydantic import Field

from backend.common.schema import SchemaBase


class SpeechCatalogModelSummary(SchemaBase):
    """单个模型摘要（管理端展示，非权威）。"""

    model_id: str = Field(description='稳定模型标识')
    model_version: str = Field(description='模型修订')
    display_name: str = Field(default='', description='用户可读名称')
    engine: str = Field(default='', description='worker 引擎标识')
    platforms: list[str] = Field(default_factory=list, description='包平台三元组 os-arch-acceleration')
    package_count: int = Field(default=0, description='平台包数量')


class SpeechCatalogNodeResponse(SchemaBase):
    """节点拉取签名 catalog 出参（Owner JWT，全平台同一权威单行）。"""

    catalog_json: str | None = Field(
        default=None,
        description='签名 catalog 逐字节原文（daemon 验签用）；未发布时为 None，daemon 保持未装配态',
    )
    revision: str = Field(default='', description='catalog 原文指纹 sha256[:16]，daemon 比对重拉')
    catalog_version: str = Field(default='', description='catalog 内声明版本号（展示/回滚判定）')
    published_time: datetime | None = Field(default=None, description='最后发布时间')


class SpeechCatalogPublishResponse(SchemaBase):
    """CI 发布签名 catalog + 模型 zip 出参。"""

    revision: str = Field(description='新 catalog 原文指纹 sha256[:16]')
    catalog_version: str = Field(description='catalog 内声明版本号')
    object_key: str = Field(description='模型 zip 落公开桶的对象 key')
    download_url: str = Field(description='模型 zip 公开 https 直链（须与 catalog 内嵌 URL 一致）')
    size: int = Field(description='上传 zip 字节数')
    sha256: str = Field(description='服务端据落桶字节现算的 sha256')
    models: list[SpeechCatalogModelSummary] = Field(default_factory=list, description='catalog 内模型摘要')
