"""Regression: HASN Agent JSONB defaults must be JSON-serializable values."""
from __future__ import annotations

import pytest

from backend.app.hasn.model.hasn_agents import HasnAgents


@pytest.mark.asyncio
async def test_hasn_agents_tags_default_is_list_instance_not_type() -> None:
    """`tags` should default to `[]`, not `<class 'list'>`."""
    agent = HasnAgents(
        hasn_id='a_test_default',
        star_id='100001#default',
        owner_id='h_owner_default',
        display_name='默认 Agent',
        agent_name='default_agent',
        type='desktop',
        role='specialist',
        api_key_hash='hash',
        status='active',
        created_via='client',
    )

    assert isinstance(agent.tags, list)
    assert agent.tags == []
