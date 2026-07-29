from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from backend.app.hasn_growth.service.audit_service import AuditPayloadLeakError, assert_audit_payload_safe
from backend.app.hasn_growth.service.business_service import _contact_field_requirements, _mask_email, _mask_phone
from backend.app.hasn_growth.service.cleaner_service import clean_raw_record, normalize_email, normalize_phone
from backend.app.hasn_growth.service.dedupe_service import InMemoryLeadStore, upsert_lead
from backend.app.hasn_growth.service.export_service import build_csv_export
from backend.app.hasn_growth.service.firecrawl_client import (
    FirecrawlClient,
    FirecrawlHTTPError,
    FirecrawlTransportError,
)
from backend.app.hasn_growth.service.pii import mask_contact_fields
from backend.app.hasn_growth.service.provider_registry import PROVIDERS, CrawlRequest, get_provider
from backend.app.hasn_growth.service.retention_service import archive_expired_contacts
from backend.app.hasn_growth.service.scoring_service import score_cleaned_lead


def test_normalize_email_handles_gmail_rules_and_preserves_outlook_dots() -> None:
    assert normalize_email(' First.Last+sales@GoogleMail.com ') == 'firstlast@gmail.com'
    assert normalize_email('first.last+sales@outlook.com') == 'first.last@outlook.com'
    assert normalize_email('test@example.com') is None


def test_normalize_phone_outputs_e164_for_cn_us_and_rejects_invalid() -> None:
    assert normalize_phone('138 1234 5678', country_hint='CN') == '+8613812345678'
    assert normalize_phone('(415) 555-2671', country_hint='US') == '+14155552671'
    assert normalize_phone('020 7946 0018', country_hint='GB') == '+442079460018'
    assert normalize_phone('abc', country_hint='CN') is None


def test_cleaner_prefers_structured_payload_and_accepts_email_or_phone_by_default() -> None:
    cleaned = clean_raw_record(
        {
            'structured_payload': {
                'company_name': 'Acme Ltd',
                'emails': [' Sales.Team+cn@gmail.com '],
                'phones': ['138 1234 5678'],
                'website': 'https://acme.example',
            },
            'markdown': 'Contact Sales.Team+cn@gmail.com 138 1234 5678',
            'source_url': 'https://acme.example/contact',
            'source_type': 'public_web',
        },
        min_contact_fields=['email', 'phone'],
        country_hint='CN',
    )

    assert cleaned.accepted is True
    assert cleaned.rejected_reason is None
    assert cleaned.email_normalized == 'salesteam@gmail.com'
    assert cleaned.phone_normalized == '+8613812345678'
    assert cleaned.metadata['email_candidates'] == [' Sales.Team+cn@gmail.com ']


@pytest.mark.parametrize(
    ('structured_payload', 'accepted', 'expected_reason'),
    [
        ({'company_name': 'Only Email Inc', 'emails': ['sales@only-email.test']}, True, None),
        ({'company_name': 'Only Phone Inc', 'phones': ['138 1234 5678']}, True, None),
        ({'company_name': 'No Contact Inc'}, False, 'missing_both'),
    ],
)
def test_cleaner_accepts_email_or_phone_and_rejects_only_when_both_missing(
    structured_payload: dict,
    accepted: bool,
    expected_reason: str | None,
) -> None:
    cleaned = clean_raw_record(
        {
            'structured_payload': structured_payload,
            'markdown': 'No public contact here.',
            'source_url': 'https://nocontact.example',
            'source_type': 'public_web',
        },
        min_contact_fields=['email', 'phone'],
        country_hint='CN',
    )

    assert cleaned.accepted is accepted
    assert cleaned.rejected_reason == expected_reason


def test_business_config_accepts_required_contact_fields_alias() -> None:
    assert _contact_field_requirements({'required_contact_fields': ['email']}) == ['email']
    assert _contact_field_requirements({'min_contact_fields': ['phone']}) == ['phone']
    assert _contact_field_requirements({}) == ['email', 'phone']


def test_scoring_is_deterministic_and_rewards_traceable_contact_data() -> None:
    cleaned = clean_raw_record(
        {
            'structured_payload': {
                'company_name': 'Score Co',
                'emails': ['hello@score.co'],
                'phones': ['(415) 555-2671'],
                'website': 'https://score.co',
                'address': '1 Market St',
                'industry': 'SaaS',
            },
            'markdown': 'hello@score.co (415) 555-2671 1 Market St',
            'source_url': 'https://score.co/contact',
            'source_type': 'public_web',
            'llm_confidence': 0.91,
        },
        min_contact_fields=['email', 'phone'],
        country_hint='US',
    )

    assert score_cleaned_lead(cleaned) == 100


def test_dedupe_uses_email_phone_domain_order_globally() -> None:
    """统一线索池：全局去重（仅按规整 email/phone/domain，不含 scope/user）。同一线索全局只一份。"""
    store = InMemoryLeadStore()
    first = clean_raw_record(
        {
            'structured_payload': {
                'company_name': 'Same Co',
                'emails': ['sales@example.org'],
                'phones': ['(415) 555-2671'],
            },
            'source_url': 'https://example.org/contact',
            'source_type': 'public_web',
        },
        min_contact_fields=['email', 'phone'],
        country_hint='US',
    )
    inserted = upsert_lead(store, first, keyword='crm')

    second = clean_raw_record(
        {
            'structured_payload': {
                'company_name': 'Same Co other',
                'emails': ['other@example.org'],
                'phones': ['(415) 555-2672'],
                'website': 'https://example.org',
            },
            'source_url': 'https://example.org/about',
            'source_type': 'public_web',
        },
        min_contact_fields=['email', 'phone'],
        country_hint='US',
    )
    duplicate_by_domain = upsert_lead(store, second, keyword='crm')
    # 再插同一条 first：统一池全局去重 → 复用（不再因 scope 另起一行）
    reinsert_same = upsert_lead(store, first, keyword='crm')

    assert inserted.created is True
    assert duplicate_by_domain.created is False
    assert duplicate_by_domain.match_dimension == 'domain'
    assert reinsert_same.created is False
    assert reinsert_same.match_dimension == 'email'
    assert len(store.contacts) == 1


def test_provider_registry_contains_five_sources_and_rejects_unknown() -> None:
    assert {'maps', 'yellow_pages', 'social_media', 'b2b', 'public_web'} <= set(PROVIDERS)
    provider = get_provider('public_web')
    assert provider.source_type == 'public_web'

    with pytest.raises(KeyError):
        get_provider('unknown')


@pytest.mark.asyncio
async def test_provider_returns_crawled_items_from_firecrawl_client() -> None:
    class FakeFirecrawl:
        # 方案 A 主路径：firecrawl 只抓 markdown；这里直接连 structured_payload 一并返回，
        # _apply_backend_llm 见已有 structured_payload + llm_extractor=None 即短路（不另调后端 LLM）。
        async def scrape_markdown(self, url: str):
            return {
                'source_url': url,
                'title': 'Public web result',
                'markdown': 'sales@example.org (415) 555-2671',
                'structured_payload': {'emails': ['sales@example.org'], 'phones': ['(415) 555-2671']},
                'extract_mode': 'scrape_json',
                'attempt_count': 1,
            }

        async def search(self, query: str, *, limit: int = 5) -> list[dict]:
            raise AssertionError('url keywords should not search')

    provider = get_provider('public_web')
    items = await provider.crawl(
        CrawlRequest(job_id=1, keyword='example.com', source_type='public_web'),
        firecrawl_client=cast(Any, FakeFirecrawl()),
    )

    assert len(items) == 1
    assert items[0].source_url == 'https://example.com'
    assert items[0].structured_payload == {'emails': ['sales@example.org'], 'phones': ['(415) 555-2671']}


@pytest.mark.asyncio
async def test_provider_searches_keyword_then_scrapes_result_urls() -> None:
    calls: list[tuple[str, str | int]] = []

    class FakeFirecrawl:
        async def search(self, query: str, *, limit: int = 5) -> list[dict]:
            calls.append(('search', query))
            calls.append(('limit', limit))
            return [
                {'url': 'https://acme.example/contact', 'title': 'Acme Contact'},
                {'url': 'https://beta.example/contact', 'title': 'Beta Contact'},
            ]

        async def scrape_markdown(self, url: str):
            calls.append(('scrape', url))
            return {
                'source_url': url,
                'title': f'{url} title',
                'markdown': 'sales@company.test (415) 555-2671',
                'structured_payload': {'emails': ['sales@company.test'], 'phones': ['(415) 555-2671']},
                'extract_mode': 'scrape_json',
                'attempt_count': 1,
            }

    provider = get_provider('public_web')
    items = await provider.crawl(
        CrawlRequest(
            job_id=1,
            keyword='深圳 工业机器人 集成商 联系方式',
            source_type='public_web',
            max_results=2,
            config={'firecrawl_options': {'search_limit': 5}},
        ),
        firecrawl_client=cast(Any, FakeFirecrawl()),
    )

    assert calls == [
        ('search', '深圳 工业机器人 集成商 联系方式'),
        ('limit', 5),
        ('scrape', 'https://acme.example/contact'),
        ('scrape', 'https://beta.example/contact'),
    ]
    assert [item.source_url for item in items] == ['https://acme.example/contact', 'https://beta.example/contact']


@pytest.mark.asyncio
async def test_provider_can_use_firecrawl_extract_mode_from_options() -> None:
    calls: list[tuple[str, str | list[str], str, str]] = []

    class FakeFirecrawl:
        async def scrape_lead_json(self, url: str, schema_version: str, prompt_version: str):
            calls.append(('scrape', url, schema_version, prompt_version))
            return {}

        async def extract_leads(self, urls: list[str], schema_version: str, prompt_version: str):
            calls.append(('extract', urls, schema_version, prompt_version))
            return {
                'source_url': urls[0],
                'structured_payload': {
                    'company_name': 'IANA',
                    'emails': ['iana@iana.org'],
                    'phones': ['+1-424-254-5300'],
                },
                'extract_mode': 'extract',
                'llm_schema_version': schema_version,
                'llm_prompt_version': prompt_version,
                'attempt_count': 1,
            }

        async def search(self, query: str, *, limit: int = 5) -> list[dict]:
            raise AssertionError('url keywords should not search')

    provider = get_provider('public_web')
    items = await provider.crawl(
        CrawlRequest(
            job_id=1,
            keyword='https://www.iana.org/contact',
            source_type='public_web',
            config={
                'firecrawl_options': {
                    'extract_mode': 'extract',
                    'schema_version': 'lead_v2',
                    'prompt_version': 'lead_prompt_v2',
                }
            },
        ),
        firecrawl_client=cast(Any, FakeFirecrawl()),
    )

    assert calls == [('extract', ['https://www.iana.org/contact'], 'lead_v2', 'lead_prompt_v2')]
    assert items
    assert items[0].extract_mode == 'extract'
    assert items[0].structured_payload is not None
    assert items[0].structured_payload['emails'] == ['iana@iana.org']


@pytest.mark.asyncio
async def test_crawl_stream_stops_early_when_should_continue_returns_false() -> None:
    """2.4 真流式收口：should_continue() 转 False 后 generator 不再抓后续候选 URL（省 firecrawl/LLM 成本）。

    模拟 business_service.run_job 的「够 N 没」判定：拿够 3 条即停。10 个候选 URL 只应抓 3 个，
    剩余 7 个不被 scrape（一次性 crawl 会全抓 → 这正是 2.4 要消除的冗余）。
    """
    scraped: list[str] = []

    class FakeFirecrawl:
        async def search(self, query: str, *, limit: int = 5) -> list[dict]:
            return [{'url': f'https://site{i}.example/contact'} for i in range(10)]

        async def scrape_markdown(self, url: str):
            scraped.append(url)
            return {
                'source_url': url,
                'markdown': f'sales@{url} 138 1234 5678',
                'structured_payload': {'emails': ['x@y.z']},
            }

    provider = get_provider('public_web')
    # should_continue 以已抓数（scraped）为判据：抓下一条前查，凑够 3 条即停（每抓一条 scraped 先 +1，再 yield）。
    yielded: list = [
        item
        async for item in provider.crawl_stream(
            CrawlRequest(
                job_id=1,
                keyword='工业机器人 集成商 联系方式',
                source_type='public_web',
                max_results=3,
                config={'firecrawl_options': {'search_limit': 10}},
            ),
            firecrawl_client=cast(Any, FakeFirecrawl()),
            should_continue=lambda: len(scraped) < 3,
        )
    ]

    assert len(yielded) == 3
    assert len(scraped) == 3  # 仅抓 3 个，剩 7 个候选未抓 → 早停省成本
    assert scraped == [f'https://site{i}.example/contact' for i in range(3)]


@pytest.mark.asyncio
async def test_crawl_returns_all_candidates_when_no_should_continue() -> None:
    """向后兼容：crawl()（不传 should_continue）仍抓全部候选并返回列表。"""
    scraped: list[str] = []

    class FakeFirecrawl:
        async def search(self, query: str, *, limit: int = 5) -> list[dict]:
            return [{'url': f'https://site{i}.example/contact'} for i in range(4)]

        async def scrape_markdown(self, url: str):
            scraped.append(url)
            return {'source_url': url, 'markdown': 'sales@x.y', 'structured_payload': {'emails': ['a@b.c']}}

    provider = get_provider('public_web')
    items = await provider.crawl(
        CrawlRequest(
            job_id=1,
            keyword='工业机器人',
            source_type='public_web',
            config={'firecrawl_options': {'search_limit': 4}},
        ),
        firecrawl_client=cast(Any, FakeFirecrawl()),
    )

    assert len(items) == 4
    assert len(scraped) == 4


@pytest.mark.asyncio
async def test_firecrawl_retries_only_retryable_failures() -> None:
    calls: list[str] = []

    async def sender(method: str, url: str, payload: dict, headers: dict, timeout: float):
        calls.append(url)
        if len(calls) == 1:
            raise FirecrawlTransportError('timeout')
        return {'status_code': 200, 'json': {'markdown': 'ok', 'metadata': {'title': 'Ok'}}}

    client = FirecrawlClient(api_key='secret', sender=sender, sleep=lambda _: None, jitter=lambda: 0)
    result = await client.scrape_markdown('https://example.com')

    assert result['markdown'] == 'ok'
    assert result['attempt_count'] == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_firecrawl_sends_scrape_json_payload_and_bearer_token() -> None:
    requests: list[tuple[str, str, dict, dict, float]] = []

    async def sender(method: str, url: str, payload: dict, headers: dict, timeout: float):
        requests.append((method, url, payload, headers, timeout))
        return {
            'status_code': 200,
            'json': {
                'data': {
                    'url': 'https://www.iana.org/contact',
                    'json': {'emails': ['iana@iana.org'], 'phones': ['+1-424-254-5300']},
                },
            },
        }

    client = FirecrawlClient(api_key='secret-token', timeout_seconds=12, sender=sender)
    result = await client.scrape_lead_json('https://www.iana.org/contact', 'lead_v1', 'lead_extract_v1')

    method, url, payload, headers, timeout = requests[0]
    assert method == 'POST'
    assert url == 'https://firecrawl.dcfuture.com.cn/v1/scrape'
    assert headers['Authorization'] == 'Bearer secret-token'
    assert timeout == 12
    assert payload['formats'] == ['markdown', 'html', 'json']
    assert payload['jsonOptions']['schema']['properties']['emails']['type'] == 'array'
    assert 'lead_extract_v1' in payload['jsonOptions']['prompt']
    assert result['structured_payload'] == {'emails': ['iana@iana.org'], 'phones': ['+1-424-254-5300']}


@pytest.mark.asyncio
async def test_firecrawl_retries_retryable_http_statuses_only() -> None:
    calls = 0

    async def sender(method: str, url: str, payload: dict, headers: dict, timeout: float):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {'status_code': 429, 'json': {'error': 'rate limited'}}
        return {'status_code': 200, 'json': {'data': {'markdown': 'ok'}}}

    client = FirecrawlClient(sender=sender, sleep=lambda _: None, jitter=lambda: 0, max_retries=2)
    result = await client.scrape_markdown('https://example.com')

    assert calls == 2
    assert result['attempt_count'] == 2


@pytest.mark.asyncio
async def test_firecrawl_extract_sends_prompt_schema_payload() -> None:
    requests: list[dict] = []

    async def sender(method: str, url: str, payload: dict, headers: dict, timeout: float):
        requests.append(payload)
        return {'status_code': 200, 'json': {'data': {'emails': ['iana@iana.org'], 'phones': ['+1-424-254-5300']}}}

    client = FirecrawlClient(sender=sender)
    result = await client.extract_leads(['https://www.iana.org/contact'], 'lead_v1', 'lead_extract_v1')

    assert requests[0]['urls'] == ['https://www.iana.org/contact']
    assert requests[0]['schema']['properties']['phones']['items']['type'] == 'string'
    assert 'lead_extract_v1' in requests[0]['prompt']
    assert result['extract_mode'] == 'extract'
    assert result['structured_payload'] == {'emails': ['iana@iana.org'], 'phones': ['+1-424-254-5300']}


@pytest.mark.asyncio
async def test_firecrawl_search_sends_query_and_normalizes_result_urls() -> None:
    requests: list[tuple[str, str, dict]] = []

    async def sender(method: str, url: str, payload: dict, headers: dict, timeout: float):
        requests.append((method, url, payload))
        return {
            'status_code': 200,
            'json': {
                'success': True,
                'data': [
                    {'url': 'https://acme.example/contact', 'title': 'Acme'},
                    {'sourceURL': 'https://beta.example/contact', 'metadata': {'title': 'Beta'}},
                    {'title': 'Missing URL'},
                ],
            },
        }

    client = FirecrawlClient(sender=sender)
    results = await client.search('工业机器人 集成商 联系方式', limit=3)

    assert requests == [
        (
            'POST',
            'https://firecrawl.dcfuture.com.cn/v1/search',
            {'query': '工业机器人 集成商 联系方式', 'limit': 3},
        )
    ]
    assert results == [
        {
            'url': 'https://acme.example/contact',
            'title': 'Acme',
            'raw_payload': {'url': 'https://acme.example/contact', 'title': 'Acme'},
        },
        {
            'url': 'https://beta.example/contact',
            'title': 'Beta',
            'raw_payload': {'sourceURL': 'https://beta.example/contact', 'metadata': {'title': 'Beta'}},
        },
    ]


@pytest.mark.asyncio
async def test_firecrawl_does_not_retry_non_retryable_4xx() -> None:
    calls = 0

    async def sender(method: str, url: str, payload: dict, headers: dict, timeout: float):
        nonlocal calls
        calls += 1
        return {'status_code': 404, 'json': {'error': 'not found'}}

    client = FirecrawlClient(sender=sender, sleep=lambda _: None)

    with pytest.raises(FirecrawlHTTPError) as exc_info:
        await client.scrape_markdown('https://example.com/missing')

    assert calls == 1
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_firecrawl_extract_treats_top_level_data_as_structured_payload() -> None:
    async def sender(method: str, url: str, payload: dict, headers: dict, timeout: float):
        return {
            'status_code': 200,
            'json': {
                'success': True,
                'data': {
                    'company_name': 'IANA',
                    'emails': ['iana@iana.org'],
                    'phones': ['+1-424-254-5300'],
                    'website': 'https://www.iana.org',
                },
            },
        }

    client = FirecrawlClient(sender=sender, sleep=lambda _: None)
    result = await client.extract_leads(['https://www.iana.org/contact'], 'lead_v1', 'lead_extract_v1')

    assert result['structured_payload'] == {
        'company_name': 'IANA',
        'emails': ['iana@iana.org'],
        'phones': ['+1-424-254-5300'],
        'website': 'https://www.iana.org',
    }
    assert result['raw_payload'] == result['structured_payload']


def test_audit_payload_rejects_plaintext_pii_but_allows_hashes() -> None:
    assert_audit_payload_safe({'target_emails_sha256': ['a' * 64], 'total_count': 1})

    with pytest.raises(AuditPayloadLeakError):
        assert_audit_payload_safe({'email': 'foo@example.com'})

    with pytest.raises(AuditPayloadLeakError):
        assert_audit_payload_safe({'phone': '+14155552671'})


def test_default_contact_view_masks_name_normalized_fields_and_nested_pii() -> None:
    masked = mask_contact_fields(
        {
            'contact_name': '王小明',
            'email': 'sales@example.com',
            'email_normalized': 'sales@example.com',
            'phone': '13800138000',
            'phone_normalized': '13800138000',
            'address': '北京市朝阳区',
            'profile_json': {
                'note': '回电 13800138000，邮箱 sales@example.com',
            },
        },
        reveal=False,
    )
    assert masked['contact_name'] == '王**'
    assert masked['email'] == masked['email_normalized'] == 's***@example.com'
    assert masked['phone'] == masked['phone_normalized'] == '1380****8000'
    assert masked['address'] is None
    assert masked['profile_json']['note'] == '回电 [已脱敏电话]，邮箱 [已脱敏邮箱]'


def test_export_writes_only_masked_csv_snapshot_and_safe_audit_payload() -> None:
    contacts = [
        {'id': 1, 'lead_no': 'L001', 'company_name': 'Export Co', 'email': 'sales@export.co', 'phone': '+14155552671'},
    ]
    export = build_csv_export(
        contacts,
        batch_no='EXP001',
        user_id=9,
        filter_payload={'keyword': 'export'},
        now=datetime(2026, 5, 10, tzinfo=UTC),
    )

    assert export.batch['total_count'] == 1
    assert export.items[0]['snapshot']['email'] == 's***@export.co'
    assert export.items[0]['snapshot']['phone'] == '+141****2671'
    assert 'sales@export.co' not in export.csv_text
    assert '+14155552671' not in export.csv_text
    assert export.audit_log['event_type'] == 'export'
    assert 'sales@export.co' not in str(export.audit_log['payload'])


def test_archive_expired_contacts_anonymizes_uncontacted_only() -> None:
    now = datetime(2026, 5, 10, tzinfo=UTC)
    contacts = [
        {
            'id': 1,
            'status': 'new',
            'archived_at': now - timedelta(days=1),
            'email': 'old@example.com',
            'email_normalized': 'old@example.com',
            'phone': '+14155552671',
            'phone_normalized': '+14155552671',
        },
        {
            'id': 2,
            'status': 'contacted',
            'archived_at': now - timedelta(days=1),
            'email': 'keep@example.com',
            'email_normalized': 'keep@example.com',
            'phone': '+14155552672',
            'phone_normalized': '+14155552672',
        },
    ]

    archived = archive_expired_contacts(contacts, now=now)

    assert archived == 1
    assert contacts[0]['status'] == 'archived'
    assert contacts[0]['email'] is None
    assert contacts[0]['phone_normalized'] is None
    assert contacts[1]['email'] == 'keep@example.com'


def test_pii_masking_for_business_list_views() -> None:
    assert _mask_email('sales@example.com') == 's***@example.com'
    assert _mask_email(None) is None
    assert _mask_phone('+8613812345678') == '+861****5678'
    assert _mask_phone(None) is None
