"""Open API — 统一支付回调 POST /notify/{channelId}

流程:
1. channelId -> 查 pay_channel -> 拿到 code + config
2. 根据 code 选 PayClient -> 用 config 验签 + 解析
3. 提取 order_no / trade_no / pay_amount
4. 调用 pay_order_service.handle_pay_notify (幂等)
5. 返回 "success" / {"code":"SUCCESS"}
"""

import json

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from fastapi.responses import PlainTextResponse

from backend.app.billing.crud.crud_pay_channel import pay_channel_dao
from backend.app.billing.crud.crud_pay_merchant import pay_merchant_dao
from backend.app.billing.service.pay_contract_service import pay_contract_service
from backend.app.billing.service.pay_order_service import get_pay_client, pay_order_service
from backend.common.log import log
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


def _text_form_data(form_data: Any) -> dict[str, str]:
    """把支付渠道表单严格归一为文本字典；文件字段按非法回调拒绝。"""
    data: dict[str, str] = {}
    for key, value in form_data.multi_items():
        if not isinstance(value, str):
            raise ValueError(f'支付回调字段 {key} 必须是文本')
        data[key] = value
    return data


def _wechat_headers(request: Request) -> dict[str, str]:
    """把请求头**整份**交给 wechatpayv3 验签，不再手挑几个。

    ⚠️ 这里曾手工挑 4 个头（Timestamp / Nonce / Signature / Serial），**漏了
    `Wechatpay-Signature-Type`**。而 `wechatpayv3/core.py::_verify_signature` 第一件事就是：

        signature_type = headers.get(signature_type_mark, '')
        if signature_type != 'WECHATPAY2-SHA256-RSA2048':
            raise Exception(f'wechatpayv3 does not support this algorithm: {signature_type}')

    于是**每一个**微信回调都在验签前就抛异常，日志里是「does not support this algorithm: 」
    （冒号后为空）——微信支付回调从来没有成功过一次，用户付了钱订单永远停在待支付。
    2026-08-21 用真实 1 分钱支付复现并定位。

    改成整份透传而不是补上第 5 个头，是因为「漏一个头」这件事会重演：库支持三套头命名
    （原生 / django / fastapi），Starlette 的 header key 是小写，整份传进去正好命中
    fastapi 那一支，将来微信再加头也不用改这里。
    """
    return dict(request.headers)


def _wechat_resource(notify_data: Any) -> dict[str, Any]:
    """取出微信回调里**真正的业务体**。

    ⚠️ `wechatpayv3` 的 `callback()` 返回的是**外层信封**，解密后的业务体被塞进 `resource`：

        data = json.loads(body)                      # {'id','event_type','resource':{密文},...}
        data.update({'resource': json.loads(result)}) # ← 明文业务体在这里
        return data

    支付分支曾直接读 `notify_data.get('trade_state')`——顶层没有这个字段，**永远是 None**，
    于是从不履约；而代码接着照样给微信回 `{"code":"SUCCESS"}`，微信收到成功就**不再重试**。
    钱付了、订单永远待支付、没有任何告警——这是三个 bug 里最严重的一个，因为它连
    「靠微信重试自愈」的机会都亲手关掉了。2026-08-21 用真实支付复现。

    退款分支当时是对的（它解了 `resource`），支付与签约两处不是。
    """
    if not isinstance(notify_data, dict):
        return {}
    resource = notify_data.get('resource')
    return resource if isinstance(resource, dict) else notify_data


@router.post(
    '/notify/{channel_id}',
    summary='统一支付回调',
    response_class=PlainTextResponse,
 name='open_unified_pay_notify')
async def unified_pay_notify(
    request: Request,
    db: CurrentSessionTransaction,
    channel_id: Annotated[int, Path(description='支付渠道 ID')],
) -> PlainTextResponse:
    """统一支付回调 — 微信/支付宝共用同一入口，靠 channelId 区分"""
    channel = None
    try:
        # 1. 查渠道
        channel = await pay_channel_dao.get(db, channel_id)
        if not channel:
            log.error(f'支付回调: 渠道 {channel_id} 不存在')
            return PlainTextResponse('fail', status_code=200)

        # 查关联商户密钥
        merchant_config = None
        if channel.merchant_id:
            merchant = await pay_merchant_dao.get(db, channel.merchant_id)
            if merchant:
                merchant_config = merchant.config

        code = channel.code
        client = get_pay_client(channel, merchant_config=merchant_config)

        # 2. 验签 + 解析
        if code.startswith('wx'):
            body = await request.body()
            raw_data = body.decode('utf-8')
            log.info(f'微信支付回调 channel={channel_id}: {raw_data[:500]}')
            headers = _wechat_headers(request)
            notify_data = client.verify_callback(headers, raw_data)
            resource = _wechat_resource(notify_data)

            trade_state = resource.get('trade_state')
            if trade_state == 'SUCCESS':
                order_no = resource['out_trade_no']
                channel_order_no = resource['transaction_id']
                pay_amount = resource['amount']['total']
                channel_user_id = (resource.get('payer') or {}).get('openid')
                await pay_order_service.handle_pay_notify(
                    db=db, order_no=order_no, channel_order_no=channel_order_no,
                    pay_amount=pay_amount, channel_code=code,
                    channel_user_id=channel_user_id, raw_data=raw_data,
                )
                return PlainTextResponse('{"code":"SUCCESS","message":"成功"}', status_code=200)

            # **没真正处理就绝不回 SUCCESS**：回 SUCCESS 等于告诉微信「收到了」，
            # 它立刻停止重试，这笔钱就永久落在地上且无人知道。回 FAIL 让它继续按
            # 15s/15s/30s/3m/10m… 重试，给我们修复并自愈的窗口。
            log.error(
                f'微信支付回调未能识别为成功支付，拒收以触发重试: channel={channel_id} '
                f'event_type={notify_data.get("event_type")} trade_state={trade_state} '
                f'out_trade_no={resource.get("out_trade_no")}'
            )
            return PlainTextResponse('{"code":"FAIL","message":"回调未识别为成功支付"}', status_code=500)

        if code.startswith('alipay'):
            form_data = await request.form()
            data = _text_form_data(form_data)
            raw_data = json.dumps(data, ensure_ascii=False)
            log.info(f'支付宝回调 channel={channel_id}: {raw_data[:500]}')

            # verify_callback expects dict for alipay
            client.verify_callback({}, data)

            trade_status = data.get('trade_status')
            if trade_status in ('TRADE_SUCCESS', 'TRADE_FINISHED'):
                order_no = data['out_trade_no']
                channel_order_no = data['trade_no']
                pay_amount = int(float(data['total_amount']) * 100)
                channel_user_id = data.get('buyer_id')
                await pay_order_service.handle_pay_notify(
                    db=db, order_no=order_no, channel_order_no=channel_order_no,
                    pay_amount=pay_amount, channel_code=code,
                    channel_user_id=channel_user_id, raw_data=raw_data,
                )
            return PlainTextResponse('success', status_code=200)

        log.error(f'不支持的渠道编码: {code}')
        return PlainTextResponse('fail', status_code=200)

    except Exception as e:
        log.error(f'支付回调异常: channel={channel_id}, error={e}')
        if channel and channel.code.startswith('wx'):
            return PlainTextResponse(f'{{"code":"FAIL","message":"{str(e)[:100]}"}}', status_code=500)
        return PlainTextResponse('fail', status_code=200)


@router.post(
    '/refund-notify/{channel_id}',
    summary='统一退款回调',
    response_class=PlainTextResponse,
 name='open_unified_refund_notify')
async def unified_refund_notify(
    request: Request,
    db: CurrentSessionTransaction,
    channel_id: Annotated[int, Path(description='支付渠道 ID')],
) -> PlainTextResponse:
    """统一退款回调（MK-9）——渠道异步退款终态确认，幂等，只确认不重复回收权益。

    退款在 ``refund_order`` 发起时已同步落库（权益回收 + 订单置退款态）；本回调仅在渠道**异步**
    退款（微信退款可能先 PROCESSING 后推 ``REFUND.SUCCESS``）时把退款记录/订单推到终态。
    支付宝退款为同步返回，一般不发异步退款回调，收到时按防御式解析。
    """
    channel = None
    try:
        channel = await pay_channel_dao.get(db, channel_id)
        if not channel:
            log.error(f'退款回调: 渠道 {channel_id} 不存在')
            return PlainTextResponse('fail', status_code=200)

        merchant_config = None
        if channel.merchant_id:
            merchant = await pay_merchant_dao.get(db, channel.merchant_id)
            if merchant:
                merchant_config = merchant.config

        code = channel.code
        client = get_pay_client(channel, merchant_config=merchant_config)

        if code.startswith('wx'):
            body = await request.body()
            raw_data = body.decode('utf-8')
            log.info(f'微信退款回调 channel={channel_id}: {raw_data[:500]}')
            headers = _wechat_headers(request)
            notify_data = client.verify_callback(headers, raw_data)
            # 微信 V3 退款回调：event_type=REFUND.SUCCESS/ABNORMAL/CLOSED，resource 内含 out_refund_no/refund_status。
            resource_value = notify_data.get('resource') if isinstance(notify_data, dict) else None
            resource: dict[str, Any] = (
                resource_value
                if isinstance(resource_value, dict)
                else notify_data
                if isinstance(notify_data, dict)
                else {}
            )
            out_refund_no = resource.get('out_refund_no')
            refund_status = resource.get('refund_status') or (
                'SUCCESS' if notify_data.get('event_type') == 'REFUND.SUCCESS' else notify_data.get('event_type', '')
            )
            channel_refund_no = resource.get('refund_id')
            if out_refund_no:
                await pay_order_service.confirm_refund_notify(
                    db=db, refund_no=out_refund_no, refund_status=refund_status, channel_refund_no=channel_refund_no,
                )
            return PlainTextResponse('{"code":"SUCCESS","message":"成功"}', status_code=200)

        if code.startswith('alipay'):
            form_data = await request.form()
            data = _text_form_data(form_data)
            raw_data = json.dumps(data, ensure_ascii=False)
            log.info(f'支付宝退款回调 channel={channel_id}: {raw_data[:500]}')
            client.verify_callback({}, data)
            out_refund_no = data.get('out_request_no') or data.get('out_biz_no')
            trade_ok = data.get('trade_status') == 'TRADE_SUCCESS'
            refund_status = data.get('refund_status') or ('SUCCESS' if trade_ok else '')
            channel_refund_no = data.get('trade_no')
            if out_refund_no and refund_status:
                await pay_order_service.confirm_refund_notify(
                    db=db, refund_no=out_refund_no, refund_status=refund_status, channel_refund_no=channel_refund_no,
                )
            return PlainTextResponse('success', status_code=200)

        log.error(f'退款回调: 不支持的渠道编码 {code}')
        return PlainTextResponse('fail', status_code=200)

    except Exception as e:
        log.error(f'退款回调异常: channel={channel_id}, error={e}')
        if channel and channel.code.startswith('wx'):
            return PlainTextResponse(f'{{"code":"FAIL","message":"{str(e)[:100]}"}}', status_code=500)
        return PlainTextResponse('fail', status_code=200)


@router.post(
    '/contract-notify/{channel_id}',
    summary='统一签约/解约回调',
    response_class=PlainTextResponse,
 name='open_unified_contract_notify')
async def unified_contract_notify(
    request: Request,
    db: CurrentSessionTransaction,
    channel_id: Annotated[int, Path(description='支付渠道 ID')],
) -> PlainTextResponse:
    """统一签约/解约回调"""
    try:
        channel = await pay_channel_dao.get(db, channel_id)
        if not channel:
            return PlainTextResponse('fail', status_code=200)

        # 查关联商户密钥
        merchant_config = None
        if channel.merchant_id:
            merchant = await pay_merchant_dao.get(db, channel.merchant_id)
            if merchant:
                merchant_config = merchant.config

        code = channel.code
        client = get_pay_client(channel, merchant_config=merchant_config)

        if code.startswith('wx'):
            body = await request.body()
            raw_data = body.decode('utf-8')
            headers = _wechat_headers(request)
            notify_data = client.verify_callback(headers, raw_data)
            # 与支付分支同一个坑：change_type 也在解密后的 resource 里，不在顶层。
            resource = _wechat_resource(notify_data)
            change_type = resource.get('change_type')
            if change_type == 'ADD':
                contract_no = resource.get('out_contract_code')
                channel_contract_id = resource.get('contract_id')
                if contract_no and channel_contract_id:
                    await pay_contract_service.handle_sign_notify(db=db, contract_no=contract_no, channel_contract_id=channel_contract_id)
            elif change_type == 'DELETE':
                contract_no = resource.get('out_contract_code')
                if contract_no:
                    await pay_contract_service.handle_unsign_notify(db=db, contract_no=contract_no)
            else:
                log.error(
                    f'微信签约回调未识别 change_type，拒收以触发重试: channel={channel_id} '
                    f'event_type={notify_data.get("event_type")} change_type={change_type}'
                )
                return PlainTextResponse('{"code":"FAIL","message":"签约回调未识别"}', status_code=500)
            return PlainTextResponse('{"code":"SUCCESS","message":"成功"}', status_code=200)

        if code.startswith('alipay'):
            form_data = await request.form()
            data = _text_form_data(form_data)
            client.verify_callback({}, data)
            status = data.get('status')
            if status == 'NORMAL':
                contract_no = data.get('external_agreement_no')
                agreement_no = data.get('agreement_no')
                if contract_no and agreement_no:
                    await pay_contract_service.handle_sign_notify(db=db, contract_no=contract_no, channel_contract_id=agreement_no)
            elif status == 'UNSIGN':
                contract_no = data.get('external_agreement_no')
                if contract_no:
                    await pay_contract_service.handle_unsign_notify(db=db, contract_no=contract_no)
            return PlainTextResponse('success', status_code=200)

        return PlainTextResponse('fail', status_code=200)
    except Exception as e:
        log.error(f'签约回调异常: channel={channel_id}, error={e}')
        return PlainTextResponse('fail', status_code=200)
