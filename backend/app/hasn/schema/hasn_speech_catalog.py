"""通用语音模型签名目录 schema（云端权威·哑存储·单行下发）SPCAT-4。

- 节点下发出参 ``SpeechCatalogNodeResponse``：daemon 拉取，据 revision 比对决定重拉；
  ``catalog_json`` 是签名 catalog 逐字节原文（可空——未发布时为 None，daemon 保持「未装配」）。
- 暂存出参 ``SpeechPackageStageResponse``：内容寻址包的不可变登记与真实公开 HTTPS 直链。
- 发布出参 ``SpeechCatalogPublishResponse``：原子切换后的 release head、全部引用包和模型摘要。
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


class SpeechPackageStageResponse(SchemaBase):
    """CI 暂存一个内容寻址模型包的出参。"""

    package_id: int = Field(description='内容寻址包登记 ID')
    sha256: str = Field(description='服务端据上传原始字节计算的规范小写 SHA-256')
    object_key: str = Field(description='由 SHA-256 确定派生的不可变对象 key')
    download_url: str = Field(description='公共桶长效 HTTPS 直链')
    size: int = Field(description='对象真实字节数')
    already_exists: bool = Field(description='同摘要包是否已经登记并通过对象存在性复核')


class SpeechCatalogPublishResponse(SchemaBase):
    """CI 原子发布签名 catalog release 的出参。"""

    release_id: int = Field(description='不可变 release 记录 ID')
    revision: str = Field(description='catalog 原文指纹 sha256[:16]')
    release_sequence: int = Field(description='全目录单调 u64 发布序列')
    key_id: str = Field(description='签名信任环中的稳定公钥标识')
    catalog_version: str = Field(description='catalog 内声明版本号')
    idempotent: bool = Field(description='是否命中同序列同 revision 的幂等发布')
    packages: list[SpeechPackageStageResponse] = Field(
        default_factory=list,
        description='本 release 引用且已完成真实对象复核的唯一内容寻址包',
    )
    models: list[SpeechCatalogModelSummary] = Field(default_factory=list, description='catalog 内模型摘要')
