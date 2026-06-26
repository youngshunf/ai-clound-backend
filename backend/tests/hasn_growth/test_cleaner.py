"""hasn_growth 清洗服务（cleaner_service）纯函数单元测试（零 mock，无 DB 依赖）。

覆盖采集线索清洗的核心质量闸：

- ``normalize_phone``：只接受合法的中国手机/座机/400-800 服务号/带国家码国际号，拒绝
  页面里的垃圾数字串（文章 ID、阅读量、时间戳片段）。这是「采集到假电话入库」的根因修复——
  原兜底规则 ``8 <= len <= 15 and startswith(('86','1','44'))`` 把任意 1 开头的 8-15 位
  数字误判为电话，导致 ``1554800475317`` / ``10873019`` 等垃圾被当成联系方式入库。
- ``normalize_email``：NFKC 归一 + 严格格式校验 + 反垃圾（拒 example.com / test 前缀）+
  gmail 点号折叠。
- ``clean_raw_record``：准入判定（默认 email OR phone 任一即过；都无则 ``missing_both``）+
  从 markdown 文本正则兜底提取联系方式（无 LLM 也能工作）。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/07-AI获客增长应用接入设计.md。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_growth.service.cleaner_service import (
    clean_raw_record,
    normalize_email,
    normalize_phone,
)


class TestNormalizePhoneRejectsJunk:
    """页面里的非电话数字串必须被拒绝，不得当成联系方式入库（假阳性回归）。"""

    @pytest.mark.parametrize(
        'junk',
        [
            '1554800475317',  # 13 位，1 开头但非 86 国家码（原假阳性 #1）
            '10873019',  # 8 位，1 开头（原假阳性 #2）
            '1952427868563957075',  # 19 位知乎文章 ID
            '20260626120000',  # 14 位时间戳片段
            '12012345678',  # 11 位但手机第二位为 0（不存在的号段）
            '10012345678',  # 11 位但手机第二位为 0
            '99999999',  # 8 位纯数字
            '0',
            '',
        ],
    )
    def test_rejects_non_phone_digit_strings(self, junk: str) -> None:
        # Act + Assert
        assert normalize_phone(junk) is None, f'{junk!r} 不是合法电话，应被拒绝'

    def test_rejects_none(self) -> None:
        assert normalize_phone(None) is None


class TestNormalizePhoneAcceptsReal:
    """合法号码必须被正确识别并归一为 E.164。"""

    def test_china_mobile(self) -> None:
        assert normalize_phone('13800138000') == '+8613800138000'
        assert normalize_phone('18687202019') == '+8618687202019'

    def test_china_mobile_with_separators(self) -> None:
        # 分隔符/空格不影响识别
        assert normalize_phone('138 0013 8000') == '+8613800138000'
        assert normalize_phone('138-0013-8000') == '+8613800138000'

    def test_china_landline(self) -> None:
        assert normalize_phone('020-87654321') == '+862087654321'  # 广州
        assert normalize_phone('0755-12345678') == '+8675512345678'  # 深圳
        assert normalize_phone('010-12345678') == '+861012345678'  # 北京

    def test_enterprise_service_number(self) -> None:
        assert normalize_phone('400-820-8820') == '+864008208820'
        assert normalize_phone('800-810-1234') == '+868008101234'

    def test_international_with_plus(self) -> None:
        assert normalize_phone('+8613800138000') == '+8613800138000'
        assert normalize_phone('+1 415 555 0100') == '+14155550100'

    def test_country_code_86_prefix(self) -> None:
        assert normalize_phone('8613800138000') == '+8613800138000'


class TestNormalizeEmail:
    def test_accepts_valid_lowercased(self) -> None:
        assert normalize_email('Sales@GzLed.Com') == 'sales@gzled.com'
        assert normalize_email('  hr@company.cn  ') == 'hr@company.cn'

    def test_rejects_invalid_format(self) -> None:
        assert normalize_email('not-an-email') is None
        assert normalize_email('a@b') is None  # 缺 TLD
        assert normalize_email('') is None
        assert normalize_email(None) is None

    def test_rejects_placeholder_domains(self) -> None:
        # 反垃圾：example.com 与 test 前缀的占位邮箱不入库
        assert normalize_email('foo@example.com') is None
        assert normalize_email('test@company.cn') is None

    def test_gmail_dot_folding(self) -> None:
        assert normalize_email('john.doe+promo@gmail.com') == 'johndoe@gmail.com'


class TestCleanRawRecord:
    def test_extracts_phone_and_email_from_markdown(self) -> None:
        # Arrange
        raw = {
            'markdown': '广州XX光电LED显示屏厂家\n联系电话：13800138000\n商务邮箱：sales@gzled.com',
            'source_url': 'https://gzled.com/contact',
            'source_type': 'public_web',
        }
        # Act
        cleaned = clean_raw_record(raw)
        # Assert
        assert cleaned.accepted is True
        assert cleaned.rejected_reason is None
        assert cleaned.phone_normalized == '+8613800138000'
        assert cleaned.email_normalized == 'sales@gzled.com'

    def test_email_only_is_admitted(self) -> None:
        raw = {'markdown': '咨询邮箱 hr@company.cn', 'source_url': 'https://company.cn'}
        cleaned = clean_raw_record(raw)
        assert cleaned.accepted is True
        assert cleaned.email_normalized == 'hr@company.cn'
        assert cleaned.phone_normalized is None

    def test_rejects_page_without_contact(self) -> None:
        raw = {'markdown': '知乎，让每一次点击都充满意义', 'source_url': 'https://zhihu.com/p/1'}
        cleaned = clean_raw_record(raw)
        assert cleaned.accepted is False
        assert cleaned.rejected_reason == 'missing_both'

    def test_junk_numbers_never_become_phone(self) -> None:
        # 回归：反爬占位页里的长数字串（文章 ID / 阅读量 / 时间戳）不得被误判为电话入库
        raw = {
            'markdown': '阅读 10873019 发布时间 1554800475317 文章ID 20260626120000',
            'source_url': 'https://zhuanlan.zhihu.com/p/679862938',
        }
        cleaned = clean_raw_record(raw)
        assert cleaned.phone_normalized is None
        assert cleaned.accepted is False
        assert cleaned.rejected_reason == 'missing_both'
