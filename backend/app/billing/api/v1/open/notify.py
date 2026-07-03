"""Open API — 统一支付回调 POST /notify/{channelId}

流程:
1. channelId -> 查 pay_channel -> 拿到 code + config
2. 根据 code 选 PayClient -> 用 config 验签 + 解析
3. 提取 order_no / trade_no / pay_amount
4. 调用 pay_order_service.handle_pay_notify (幂等)
5. 返回 "success" / {"code":"SUCCESS"}
"""

import json

from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.responses import PlainTextResponse

from backend.app.billing.crud.crud_pay_channel import pay_channel_dao
from backend.app.billing.crud.crud_pay_merchant import pay_merchant_dao
from backend.app.billing.service.pay_contract_service import pay_contract_service
from backend.app.billing.service.pay_order_service import get_pay_client, pay_order_service
from backend.common.log import log
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


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
            headers = {
                'Wechatpay-Timestamp': request.headers.get('Wechatpay-Timestamp', ''),
                'Wechatpay-Nonce': request.headers.get('Wechatpay-Nonce', ''),
                'Wechatpay-Signature': request.headers.get('Wechatpay-Signature', ''),
                'Wechatpay-Serial': request.headers.get('Wechatpay-Serial', ''),
            }
            notify_data = client.verify_callback(headers, raw_data)

            trade_state = notify_data.get('trade_state')
            if trade_state == 'SUCCESS':
                order_no = notify_data['out_trade_no']
                channel_order_no = notify_data['transaction_id']
                pay_amount = notify_data['amount']['total']
                channel_user_id = notify_data.get('payer', {}).get('openid')
                await pay_order_service.handle_pay_notify(
                    db=db, order_no=order_no, channel_order_no=channel_order_no,
                    pay_amount=pay_amount, channel_code=code,
                    channel_user_id=channel_user_id, raw_data=raw_data,
                )
            return PlainTextResponse('{"code":"SUCCESS","message":"成功"}', status_code=200)

        if code.startswith('alipay'):
            form_data = await request.form()
            data = dict(form_data)
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
    """统一退款回调 — 预留接口"""
    log.info(f'退款回调: channel={channel_id}')
    # TODO: 实现退款回调处理
    return PlainTextResponse('success', status_code=200)


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
            headers = {
                'Wechatpay-Timestamp': request.headers.get('Wechatpay-Timestamp', ''),
                'Wechatpay-Nonce': request.headers.get('Wechatpay-Nonce', ''),
                'Wechatpay-Signature': request.headers.get('Wechatpay-Signature', ''),
                'Wechatpay-Serial': request.headers.get('Wechatpay-Serial', ''),
            }
            notify_data = client.verify_callback(headers, raw_data)
            change_type = notify_data.get('change_type')
            if change_type == 'ADD':
                contract_no = notify_data.get('out_contract_code')
                channel_contract_id = notify_data.get('contract_id')
                if contract_no and channel_contract_id:
                    await pay_contract_service.handle_sign_notify(db=db, contract_no=contract_no, channel_contract_id=channel_contract_id)
            elif change_type == 'DELETE':
                contract_no = notify_data.get('out_contract_code')
                if contract_no:
                    await pay_contract_service.handle_unsign_notify(db=db, contract_no=contract_no)
            return PlainTextResponse('{"code":"SUCCESS","message":"成功"}', status_code=200)

        if code.startswith('alipay'):
            form_data = await request.form()
            data = dict(form_data)
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
