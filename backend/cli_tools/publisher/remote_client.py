"""远程发布客户端

通过 HTTP API 发布技能和应用到远程服务器。
"""
import httpx
from dataclasses import dataclass
from pathlib import Path

from backend.cli_tools.cli.common import print_info, print_success, print_error


@dataclass
class RemotePublishResult:
    """远程发布结果"""
    success: bool
    id: str | None = None
    version: str | None = None
    package_url: str | None = None
    file_hash: str | None = None
    file_size: int | None = None
    error: str | None = None


class RemotePublishClient:
    """远程发布客户端"""
    
    def __init__(self, api_url: str, api_key: str):
        """
        初始化客户端
        
        :param api_url: API 服务器地址，如 http://localhost:8020
        :param api_key: 发布 API Key
        """
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.timeout = 120.0  # 上传超时时间
    
    async def publish_skill(
        self,
        zip_path: Path,
        version: str | None = None,
        changelog: str | None = None,
    ) -> RemotePublishResult:
        """
        发布技能包
        
        :param zip_path: ZIP 文件路径
        :param version: 版本号（可选）
        :param changelog: 更新日志（可选）
        :return: 发布结果
        """
        url = f"{self.api_url}/api/v1/marketplace/publish/skill"
        return await self._upload(url, zip_path, version, changelog)
    
    async def publish_app(
        self,
        zip_path: Path,
        version: str | None = None,
        changelog: str | None = None,
    ) -> RemotePublishResult:
        """
        发布应用包
        
        :param zip_path: ZIP 文件路径
        :param version: 版本号（可选）
        :param changelog: 更新日志（可选）
        :return: 发布结果
        """
        url = f"{self.api_url}/api/v1/marketplace/publish/app"
        return await self._upload(url, zip_path, version, changelog)
    
    async def _upload(
        self,
        url: str,
        zip_path: Path,
        version: str | None,
        changelog: str | None,
    ) -> RemotePublishResult:
        """执行上传"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # 准备文件和表单数据
                with open(zip_path, 'rb') as f:
                    files = {'file': (zip_path.name, f, 'application/zip')}
                    data = {}
                    if version:
                        data['version'] = version
                    if changelog:
                        data['changelog'] = changelog
                    
                    headers = {'X-API-Key': self.api_key}
                    
                    response = await client.post(
                        url,
                        files=files,
                        data=data,
                        headers=headers,
                    )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('code') == 200:
                        data = result['data']
                        return RemotePublishResult(
                            success=True,
                            id=data['id'],
                            version=data['version'],
                            package_url=data['package_url'],
                            file_hash=data['file_hash'],
                            file_size=data['file_size'],
                        )
                    else:
                        return RemotePublishResult(
                            success=False,
                            error=result.get('msg', '发布失败'),
                        )
                else:
                    error_msg = f"HTTP {response.status_code}"
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('msg', error_msg)
                    except:
                        pass
                    return RemotePublishResult(
                        success=False,
                        error=error_msg,
                    )
        
        except httpx.TimeoutException:
            return RemotePublishResult(
                success=False,
                error='请求超时，请检查网络连接',
            )
        except httpx.ConnectError:
            return RemotePublishResult(
                success=False,
                error=f'无法连接到服务器: {self.api_url}',
            )
        except Exception as e:
            return RemotePublishResult(
                success=False,
                error=str(e),
            )
