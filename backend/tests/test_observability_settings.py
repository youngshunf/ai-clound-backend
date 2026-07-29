from backend.core.conf import _set_production_observability_default


def test_production_enables_opentelemetry_by_default() -> None:
    values: dict[str, object] = {}
    _set_production_observability_default(values)

    assert values['GRAFANA_METRICS_ENABLE'] is True


def test_production_allows_explicit_opentelemetry_shutdown() -> None:
    values: dict[str, object] = {'GRAFANA_METRICS_ENABLE': 'false'}
    _set_production_observability_default(values)

    assert values['GRAFANA_METRICS_ENABLE'] == 'false'
