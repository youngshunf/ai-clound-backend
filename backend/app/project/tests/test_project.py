"""项目管理 API 测试

使用 pytest + starlette TestClient 进行测试
"""

import pytest

from starlette.testclient import TestClient

from backend.app.admin.tests.conftest import PYTEST_BASE_URL
from backend.main import app

# 测试用项目数据
TEST_PROJECT = {
    'name': '测试项目',
    'description': '这是一个测试项目',
    'industry': '科技',
    'sub_industries': ['人工智能', '软件开发'],
    'brand_name': '测试品牌',
    'brand_tone': '专业',
    'brand_keywords': ['AI', 'SaaS'],
    'topics': ['技术分享', '产品评测'],
    'keywords': ['人工智能', '效率工具'],
}


@pytest.fixture(scope='module')
def client():
    with TestClient(app, base_url=PYTEST_BASE_URL) as c:
        yield c


@pytest.fixture(scope='module')
def token_headers(client: TestClient):
    """获取认证 headers"""
    params = {
        'username': 'admin',
        'password': '123456',
    }
    response = client.post('/auth/login/swagger', params=params)
    if response.status_code != 200:
        pytest.skip('无法获取登录 token，跳过测试')
    token_type = response.json()['token_type']
    access_token = response.json()['access_token']
    return {'Authorization': f'{token_type} {access_token}'}


class TestProjectAPI:
    """项目管理 API 测试类"""

    created_project_id: int | None = None

    def test_create_project(self, client: TestClient, token_headers: dict) -> None:
        """测试创建项目"""
        response = client.post('/projects', json=TEST_PROJECT, headers=token_headers)
        assert response.status_code == 200

        data = response.json()
        assert data['code'] == 200
        assert data['data']['name'] == TEST_PROJECT['name']
        assert data['data']['industry'] == TEST_PROJECT['industry']
        assert 'uuid' in data['data']

        TestProjectAPI.created_project_id = data['data']['id']

    def test_get_projects(self, client: TestClient, token_headers: dict) -> None:
        """测试获取项目列表"""
        response = client.get('/projects', headers=token_headers)
        assert response.status_code == 200

        data = response.json()
        assert data['code'] == 200
        assert 'items' in data['data']
        assert isinstance(data['data']['items'], list)

    def test_get_project(self, client: TestClient, token_headers: dict) -> None:
        """测试获取项目详情"""
        if not TestProjectAPI.created_project_id:
            pytest.skip('没有创建的项目')

        response = client.get(f'/projects/{TestProjectAPI.created_project_id}', headers=token_headers)
        assert response.status_code == 200

        data = response.json()
        assert data['code'] == 200
        assert data['data']['id'] == TestProjectAPI.created_project_id

    def test_update_project(self, client: TestClient, token_headers: dict) -> None:
        """测试更新项目"""
        if not TestProjectAPI.created_project_id:
            pytest.skip('没有创建的项目')

        update_data = {'name': '更新后的项目名称', 'description': '更新后的描述'}
        response = client.put(
            f'/projects/{TestProjectAPI.created_project_id}',
            json=update_data,
            headers=token_headers,
        )
        assert response.status_code == 200
        assert response.json()['code'] == 200

    def test_set_default_project(self, client: TestClient, token_headers: dict) -> None:
        """测试设为默认项目"""
        if not TestProjectAPI.created_project_id:
            pytest.skip('没有创建的项目')

        response = client.post(
            f'/projects/{TestProjectAPI.created_project_id}/set-default',
            headers=token_headers,
        )
        assert response.status_code == 200
        assert response.json()['code'] == 200

    def test_get_default_project(self, client: TestClient, token_headers: dict) -> None:
        """测试获取默认项目"""
        response = client.get('/projects/default', headers=token_headers)
        assert response.status_code == 200

        data = response.json()
        assert data['code'] == 200

    def test_delete_project_fail_if_default(self, client: TestClient, token_headers: dict) -> None:
        """测试删除默认项目应该失败"""
        if not TestProjectAPI.created_project_id:
            pytest.skip('没有创建的项目')

        response = client.delete(
            f'/projects/{TestProjectAPI.created_project_id}',
            headers=token_headers,
        )
        # 默认项目不能删除，应返回错误
        # 根据业务逻辑，这里可能返回 403 或 400
        # 这里只验证不是成功删除
        if response.status_code == 200:
            # 如果返回成功，检查 code 应该不是 200
            assert response.json()['code'] != 200
