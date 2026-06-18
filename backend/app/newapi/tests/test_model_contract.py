from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping


def test_newapi_token_key_column_accepts_current_newapi_key_length() -> None:
    """new-api 当前 tokens.key 可到 128，映射表不能仍卡 48."""
    column = LlmNewapiUserMapping.__table__.c.newapi_token_key

    assert column.type.length >= 128
