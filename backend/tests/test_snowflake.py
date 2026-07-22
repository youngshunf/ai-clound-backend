import pytest

from backend.common.exception.errors import ServerError
from backend.utils.snowflake import Snowflake


def test_generate_rejects_initialized_instance_without_node_identifiers() -> None:
    """初始化标记与节点标识必须同时成立，避免生成无效 ID。"""
    generator = Snowflake()
    generator._initialized = True

    with pytest.raises(ServerError, match='节点标识未初始化'):
        generator.generate()


def test_generate_embeds_configured_node_identifiers() -> None:
    """生成的 ID 必须可解析回当前节点标识。"""
    generator = Snowflake()
    generator._initialized = True
    generator.datacenter_id = 3
    generator.worker_id = 7

    parsed = Snowflake.parse(generator.generate())

    assert parsed.datacenter_id == 3
    assert parsed.worker_id == 7
