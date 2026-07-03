def test_hasn_session_models_import_and_construct() -> None:
    from backend.app.hasn.model.hasn_sessions import (
        HasnSessionArtifacts,
        HasnSessionEvents,
        HasnSessions,
    )

    session = HasnSessions(conversation_id=None)
    session.id = 'sess_01'
    event = HasnSessionEvents(session_id='sess_01', event_type='session.created')
    artifact = HasnSessionArtifacts(session_id='sess_01', artifact_kind='report')

    assert session.id == 'sess_01'
    assert event.session_id == 'sess_01'
    assert artifact.artifact_kind == 'report'
