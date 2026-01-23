"""阿里云短信服务
@author Ysf
"""

import hashlib
import hmac
import json
import urllib.parse

from base64 import b64encode
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from backend.core.conf import settings


class AliyunSmsService:
    """阿里云短信服务"""

    ENDPOINT = 'https://dysmsapi.aliyuncs.com'
    VERSION = '2017-05-25'

    def __init__(self) -> None:
        self.access_key_id = settings.SMS_ALIYUN_ACCESS_KEY_ID
        self.access_key_secret = settings.SMS_ALIYUN_ACCESS_KEY_SECRET
        self.sign_name = settings.SMS_ALIYUN_SIGN_NAME
        self.template_code = settings.SMS_ALIYUN_TEMPLATE_CODE

    def _sign(self, params: dict) -> str:
        """生成签名"""
        sorted_params = sorted(params.items())
        query_string = urllib.parse.urlencode(sorted_params, quote_via=urllib.parse.quote)
        string_to_sign = f'POST&%2F&{urllib.parse.quote(query_string, safe="")}'
        key = f'{self.access_key_secret}&'
        signature = hmac.new(key.encode(), string_to_sign.encode(), hashlib.sha1).digest()
        return b64encode(signature).decode()

    async def send_code(self, phone: str, code: str) -> bool:
        """发送验证码"""
        # 开发环境直接打印验证码
        if settings.ENVIRONMENT == 'dev':
            print(f'[DEV SMS] {phone} -> {code}')
            return True

        # 生产环境检查配置
        if not all([self.access_key_id, self.access_key_secret, self.sign_name, self.template_code]):
            print('[SMS Error] 短信服务未配置')
            return False

        params = {
            'AccessKeyId': self.access_key_id,
            'Action': 'SendSms',
            'Format': 'JSON',
            'PhoneNumbers': phone,
            'SignName': self.sign_name,
            'SignatureMethod': 'HMAC-SHA1',
            'SignatureNonce': str(uuid4()),
            'SignatureVersion': '1.0',
            'TemplateCode': self.template_code,
            'TemplateParam': json.dumps({'code': code}),
            'Timestamp': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'Version': self.VERSION,
        }
        params['Signature'] = self._sign(params)

        async with httpx.AsyncClient() as client:
            resp = await client.post(self.ENDPOINT, data=params, timeout=10)
            result = resp.json()
            if result.get('Code') == 'OK':
                return True
            print(f'[SMS Error] {result}')
            return False


sms_service = AliyunSmsService()
