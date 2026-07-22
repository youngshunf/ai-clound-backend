"""支付回调载荷边界校验。"""

from io import BytesIO

import pytest

from starlette.datastructures import FormData, UploadFile

from backend.app.billing.api.v1.open.notify import _form_text_values, _optional_text


def test_pay_notify_form_only_accepts_text_values() -> None:
    values = _form_text_values(FormData([('out_trade_no', 'HX202607230001'), ('total_amount', '9.99')]))

    assert values == {'out_trade_no': 'HX202607230001', 'total_amount': '9.99'}


def test_pay_notify_form_rejects_uploaded_file() -> None:
    form_data = FormData([('out_trade_no', UploadFile(BytesIO(b'payload'), filename='callback.txt'))])

    with pytest.raises(ValueError, match='out_trade_no'):
        _form_text_values(form_data)


def test_pay_notify_optional_text_rejects_structured_value() -> None:
    with pytest.raises(ValueError, match='refund_id'):
        _optional_text({'refund_id': {'unexpected': 'object'}}, 'refund_id')
