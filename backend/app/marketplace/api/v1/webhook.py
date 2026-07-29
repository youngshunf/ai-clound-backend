"""技能市场 GitHub Webhook API。"""
import hashlib
import hmac

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Header, Request
from pydantic import BaseModel

from backend.app.marketplace.service.github_app_sync_service import github_app_sync_service
from backend.app.marketplace.service.github_sync_service import (
    collect_changed_paths,
    github_sync_service,
)
from backend.common.log import log
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.core.conf import settings
from backend.database.db import async_db_session

router = APIRouter()


class WebhookResponse(BaseModel):
    """Webhook response"""
    message: str
    synced: int = 0
    failed: int = 0


def has_skill_source_changes(commits: list[dict]) -> bool:
    """判断推送是否改动了技能或技能包来源。"""
    return source_release_required(commits) or bool(bundle_source_changes(commits))


def source_release_required(commits: list[dict]) -> bool:
    """判断推送是否需要从可信本地仓库发布技能制品。"""
    return any(
        path == '.gitmodules'
        or path == 'common-skills.yaml'
        or path == 'github'
        or path.startswith(('huanxing-skills/', 'github/'))
        for path in collect_changed_paths(commits)
    )


def bundle_source_changes(commits: list[dict]) -> set[str]:
    """返回仍需由服务器仓库同步的技能包变更路径。"""
    return {
        path
        for path in collect_changed_paths(commits)
        if path == 'common-bundles.yaml' or path.startswith('bundles/')
    }


def verify_github_signature(payload: bytes, signature: str) -> bool:
    """
    Verify GitHub webhook signature

    Args:
        payload: Request body
        signature: X-Hub-Signature-256 header value

    Returns:
        True if signature is valid
    """
    # Get webhook secret from settings
    secret = getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')
    if not secret:
        log.warning("GITHUB_WEBHOOK_SECRET not configured, skipping signature verification")
        return True  # Allow if secret not configured (for development)

    # If secret is configured but no signature provided, reject
    if not signature:
        return False

    # Signature format: sha256=<hash>
    if not signature.startswith('sha256='):
        return False

    expected_signature = signature[7:]  # Remove 'sha256=' prefix

    # Calculate HMAC
    mac = hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256)
    calculated_signature = mac.hexdigest()

    return hmac.compare_digest(calculated_signature, expected_signature)


async def _run_skill_sync_background(changed_paths: set[str] | None = None, force: bool = False) -> None:
    """后台执行遗留技能包同步（自带 DB 会话）。

    技能源码已改为可信本地仓库打包发布；该任务仅保留给尚未迁移的技能包。
    webhook 接口验签和闸门后立即返回，耗时同步不能阻塞 GitHub 响应。

    force 参数只供现有管理入口兼容使用；Webhook 始终传入技能包变更路径。
    """
    try:
        # 用 .begin() 事务上下文：成功自动 commit、异常自动 rollback。
        # sync_from_github 只 flush 不 commit，普通 async_db_session() 不自动提交，
        # 会让同步「看似成功（synced>0）实则回滚不落库」。
        async with async_db_session.begin() as db:
            if force:
                result = await github_sync_service.sync_from_github(db, force=True)
            else:
                result = await github_sync_service.sync_from_github(db, changed_paths=changed_paths or set())
        log.info(f"[Webhook] 后台技能包同步完成（{'全量重扫' if force else '增量'}）: {result}")
    except Exception as exc:
        log.error(f"[Webhook] 后台技能包同步失败: {exc}")


async def _run_template_sync_background() -> None:
    """后台执行模板全量同步（自带 DB 会话）。"""
    try:
        # 同上：.begin() 事务上下文确保同步结果真正 commit 落库（见技能同步注释）。
        async with async_db_session.begin() as db:
            result = await github_app_sync_service.sync_from_github(db, force=True)
        log.info(f"[Webhook] 后台模板同步完成: {result}")
    except Exception as exc:
        log.error(f"[Webhook] 后台模板同步失败: {exc}")


@router.post(
    '/github/skills',
    summary='GitHub Webhook for Skills',
    description='Receives GitHub push events and triggers skill sync',
)
async def github_webhook_skills(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Annotated[str | None, Header(alias='X-Hub-Signature-256')] = None,
    x_github_event: Annotated[str | None, Header(alias='X-GitHub-Event')] = None,
) -> ResponseModel | ResponseSchemaModel[WebhookResponse]:
    """
    GitHub webhook endpoint for skills

    Triggered when huanxing-hub repository receives a push event.
    验签 + 闸门后立即返回 2xx，全量同步在后台执行（GitHub 期望快速响应）。
    """
    try:
        # Read request body
        body = await request.body()

        # Verify signature
        if not verify_github_signature(body, x_hub_signature_256 or ''):
            log.error("Invalid GitHub webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

        # Parse payload
        payload = await request.json()

        # Only handle push events
        if x_github_event != 'push':
            log.info(f"Ignoring GitHub event: {x_github_event}")
            return response_base.success(data=WebhookResponse(
                message=f"Ignored event: {x_github_event}"
            ))

        commits = payload.get('commits', [])
        release_required = source_release_required(commits)
        bundle_paths = bundle_source_changes(commits)
        if not release_required and not bundle_paths:
            log.info("No skill changes detected, skipping sync")
            return response_base.success(data=WebhookResponse(
                message="No skill changes detected"
            ))

        if release_required:
            log.warning(
                "检测到技能源码变更，服务器不再克隆和打包；"
                "必须在可信 huanxing-hub 工作区运行 scripts/publish_skills.py"
            )

        if bundle_paths:
            background_tasks.add_task(_run_skill_sync_background, bundle_paths, False)
            log.info(f"已排队后台技能包同步（{len(bundle_paths)} 个变更路径）")

        if bundle_paths and release_required:
            message = (
                "Bundle sync queued; skill source release required via scripts/publish_skills.py"
            )
        elif bundle_paths:
            message = "Bundle sync queued (running in background)"
        else:
            message = "Skill source release required via scripts/publish_skills.py"
        return response_base.success(data=WebhookResponse(
            message=message
        ))

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"GitHub webhook error: {e}")
        from backend.common.response.response_code import CustomResponse
        return response_base.fail(
            res=CustomResponse(code=500, msg=str(e)),
            data=WebhookResponse(message="Webhook error", synced=0, failed=0)
        )


@router.post(
    '/github/templates',
    summary='GitHub Webhook for Templates',
    description='Receives GitHub push events and triggers template sync',
)
async def github_webhook_templates(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Annotated[str | None, Header(alias='X-Hub-Signature-256')] = None,
    x_github_event: Annotated[str | None, Header(alias='X-GitHub-Event')] = None,
) -> ResponseModel | ResponseSchemaModel[WebhookResponse]:
    """
    GitHub webhook endpoint for templates

    Triggered when huanxing-hub repository receives a push event.
    验签 + 闸门后立即返回 2xx，全量同步在后台执行。
    """
    try:
        # Read request body
        body = await request.body()

        # Verify signature
        if not verify_github_signature(body, x_hub_signature_256 or ''):
            log.error("Invalid GitHub webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

        # Parse payload
        payload = await request.json()

        # Only handle push events
        if x_github_event != 'push':
            log.info(f"Ignoring GitHub event: {x_github_event}")
            return response_base.success(data=WebhookResponse(
                message=f"Ignored event: {x_github_event}"
            ))

        # Check if templates directory was modified
        commits = payload.get('commits', [])
        has_template_changes = False

        for commit in commits:
            modified = commit.get('modified', []) + commit.get('added', []) + commit.get('removed', [])
            if any(f.startswith('templates/') and not f.startswith('templates/_') for f in modified):
                has_template_changes = True
                break

        if not has_template_changes:
            log.info("No template changes detected, skipping sync")
            return response_base.success(data=WebhookResponse(
                message="No template changes detected"
            ))

        # 异步触发：立即返回，繁重同步在后台跑
        background_tasks.add_task(_run_template_sync_background)
        log.info("Queued background template sync from GitHub webhook")
        return response_base.success(data=WebhookResponse(
            message="Template sync queued (running in background)"
        ))

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"GitHub webhook error: {e}")
        from backend.common.response.response_code import CustomResponse
        return response_base.fail(
            res=CustomResponse(code=500, msg=str(e)),
            data=WebhookResponse(message="Webhook error", synced=0, failed=0)
        )
