def test_hasn_session_models_import_and_construct() -> None:
    from backend.app.hasn.model.hasn_session_artifacts import HasnSessionArtifacts
    from backend.app.hasn.model.hasn_session_events import HasnSessionEvents
    from backend.app.hasn.model.hasn_sessions import HasnSessions

    session = HasnSessions(session_id='sess_01', conversation_id=None)
    event = HasnSessionEvents(session_id='sess_01', event_type='session.created')
    artifact = HasnSessionArtifacts(session_id='sess_01', artifact_kind='report')

    assert session.session_id == 'sess_01'
    assert event.session_id == 'sess_01'
    assert artifact.artifact_kind == 'report'
