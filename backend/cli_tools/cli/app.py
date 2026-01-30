"""应用管理 CLI 命令

提供应用的验证、发布功能。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import cappa

from backend.database.db import async_db_session
from backend.cli_tools.cli.common import (
    console,
    print_error,
    print_header,
    print_info,
    print_success,
)
from backend.cli_tools.packager.app_packager import AppPackager
from backend.cli_tools.publisher.app_publisher import AppPublisher
from backend.cli_tools.validator.app_validator import AppValidator


@cappa.command(name='validate', help='验证应用包结构', default_long=True)
@dataclass
class AppValidate:
    """验证应用包结构是否符合规范"""
    
    path: Annotated[
        Path,
        cappa.Arg(help='应用包目录路径'),
    ]
    
    def __post_init__(self) -> None:
        self.path = Path(self.path).resolve()
        if not self.path.exists():
            raise cappa.Exit(f'目录不存在: {self.path}', code=1)
    
    async def __call__(self) -> None:
        print_header('应用包验证')
        print_info(f'路径: {self.path}')
        console.print()
        
        validator = AppValidator(self.path)
        result = validator.validate()
        validator.print_result()
        
        if not result.valid:
            raise cappa.Exit(code=1)


@cappa.command(name='publish', help='发布应用到市场', default_long=True)
@dataclass
class AppPublish:
    """发布应用到市场"""
    
    path: Annotated[
        Path,
        cappa.Arg(help='应用包目录路径'),
    ]
    bump: Annotated[
        str | None,
        cappa.Arg(
            short='-b',
            help='版本递增类型: patch (1.0.0 -> 1.0.1), minor (1.0.0 -> 1.1.0), major (1.0.0 -> 2.0.0)',
        ),
    ] = None
    version: Annotated[
        str | None,
        cappa.Arg(
            short='-v',
            help='指定版本号（与 --bump 互斥）',
        ),
    ] = None
    changelog: Annotated[
        str | None,
        cappa.Arg(
            short='-c',
            help='版本更新日志',
        ),
    ] = None
    preview: Annotated[
        bool,
        cappa.Arg(
            short='-p',
            default=False,
            help='仅预览将要打包的文件，不执行发布',
        ),
    ] = False
    remote: Annotated[
        bool,
        cappa.Arg(
            short='-r',
            default=False,
            help='远程发布模式，通过 API 发布到远程服务器',
        ),
    ] = False
    api_url: Annotated[
        str | None,
        cappa.Arg(
            help='API 服务器地址，如 http://localhost:8020（远程模式必需）',
        ),
    ] = None
    api_key: Annotated[
        str | None,
        cappa.Arg(
            help='发布 API Key（远程模式必需）',
        ),
    ] = None
    
    def __post_init__(self) -> None:
        self.path = Path(self.path).resolve()
        if not self.path.exists():
            raise cappa.Exit(f'目录不存在: {self.path}', code=1)
        
        if self.bump and self.version:
            raise cappa.Exit('--bump 和 --version 不能同时使用', code=1)
        
        if self.bump and self.bump not in ('patch', 'minor', 'major'):
            raise cappa.Exit(f'无效的 --bump 类型: {self.bump}，应为 patch/minor/major', code=1)
        
        if self.remote:
            if not self.api_url:
                raise cappa.Exit('远程模式需要指定 --api-url', code=1)
            if not self.api_key:
                raise cappa.Exit('远程模式需要指定 --api-key', code=1)
    
    async def __call__(self) -> None:
        print_header('应用发布')
        print_info(f'路径: {self.path}')
        if self.remote:
            print_info(f'模式: 远程发布')
            print_info(f'服务器: {self.api_url}')
        else:
            print_info(f'模式: 本地发布')
        console.print()
        
        # 预览模式
        if self.preview:
            packager = AppPackager(self.path)
            packager.print_preview()
            return
        
        if self.remote:
            # 远程发布
            await self._publish_remote()
        else:
            # 本地发布
            await self._publish_local()
    
    async def _publish_local(self) -> None:
        """本地发布"""
        publisher = AppPublisher(self.path)
        async with async_db_session.begin() as db:
            result = await publisher.publish(
                db=db,
                bump=self.bump,
                version=self.version,
                changelog=self.changelog,
            )
        
        console.print()
        if result.success:
            print_success(f'应用发布成功!')
            print_success(f'应用 ID: {result.app_id}')
            print_success(f'版本: {result.version}')
            print_success(f'下载地址: {result.package_url}')
            print_success(f'SHA256: {result.file_hash}')
        else:
            print_error(f'发布失败: {result.error}')
            raise cappa.Exit(code=1)
    
    async def _publish_remote(self) -> None:
        """远程发布"""
        import tempfile
        from backend.cli_tools.publisher.remote_client import RemotePublishClient
        from backend.cli_tools.validator.app_validator import AppValidator
        
        # 验证应用包
        print_info('验证应用包...')
        validator = AppValidator(self.path)
        validation_result = validator.validate()
        if not validation_result.valid:
            validator.print_result()
            raise cappa.Exit(code=1)
        
        # 打包
        print_info('打包应用...')
        packager = AppPackager(self.path)
        package_result = packager.package()
        
        print_success(f'打包完成: {package_result.file_count} 个文件, {package_result.file_size} B')
        
        # 写入临时文件
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp.write(package_result.content)
            tmp_path = Path(tmp.name)
        
        # 上传
        print_info('上传到远程服务器...')
        client = RemotePublishClient(self.api_url, self.api_key)
        result = await client.publish_app(
            zip_path=tmp_path,
            version=self.version,
            changelog=self.changelog,
        )
        
        # 清理临时文件
        tmp_path.unlink(missing_ok=True)
        
        console.print()
        if result.success:
            print_success(f'应用发布成功!')
            print_success(f'应用 ID: {result.id}')
            print_success(f'版本: {result.version}')
            print_success(f'下载地址: {result.package_url}')
            print_success(f'SHA256: {result.file_hash}')
        else:
            print_error(f'发布失败: {result.error}')
            raise cappa.Exit(code=1)


@cappa.command(name='info', help='查看应用包信息', default_long=True)
@dataclass
class AppInfo:
    """查看应用包的配置信息"""
    
    path: Annotated[
        Path,
        cappa.Arg(help='应用包目录路径'),
    ]
    
    def __post_init__(self) -> None:
        self.path = Path(self.path).resolve()
        if not self.path.exists():
            raise cappa.Exit(f'目录不存在: {self.path}', code=1)
    
    async def __call__(self) -> None:
        print_header('应用包信息')
        
        validator = AppValidator(self.path)
        result = validator.validate()
        
        if result.valid and validator.manifest:
            manifest = validator.manifest
            console.print(f'  ID:          [cyan]{manifest.id}[/]')
            console.print(f'  名称:        [cyan]{manifest.name}[/]')
            console.print(f'  版本:        [cyan]{manifest.version}[/]')
            console.print(f'  类型:        [cyan]{manifest.app_type}[/]')
            console.print(f'  描述:        [cyan]{manifest.description}[/]')
            console.print(f'  定价:        [cyan]{manifest.pricing_type}[/]')
            console.print(f'  作者:        [cyan]{manifest.author_name}[/]')
            if manifest.author_email:
                console.print(f'  邮箱:        [cyan]{manifest.author_email}[/]')
            if manifest.skill_dependencies:
                console.print(f'  技能依赖:')
                for dep in manifest.skill_dependencies:
                    console.print(f'    • [cyan]{dep}[/]')
        else:
            validator.print_result()
            raise cappa.Exit(code=1)


@cappa.command(help='应用管理命令')
@dataclass
class App:
    """应用管理命令组"""
    
    subcmd: cappa.Subcommands[AppValidate | AppPublish | AppInfo]
