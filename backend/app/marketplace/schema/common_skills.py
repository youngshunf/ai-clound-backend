"""公共技能下载清单出参（doc11 §6 B3：节点级 reconciler 消费）。"""

from pydantic import Field

from backend.common.schema import SchemaBase


class CommonSkillManifestItem(SchemaBase):
    """公共技能清单条目"""

    skill_id: str = Field(description='技能 skill_id')
    fingerprint: str = Field(
        default='',
        description='内容指纹 COALESCE(content_hash,file_hash,version)；市场无版本行时为空串'
        '（诚实不臆造，消费方回落为总是重下）',
    )


class CommonSkillsManifest(SchemaBase):
    """公共技能下载清单（hasn-node daemon / 云端 reconciler 据此增量拉取公共技能）"""

    revision: str = Field(description='公共技能集合修订号 common_skills_revision')
    skills: list[CommonSkillManifestItem] = Field(default_factory=list, description='公共技能清单（skill_id 升序）')
