"""
GitHub Webhook API for Marketplace

Receives GitHub push events and triggers sync for skills and templates.
"""
import hashlib
import hmac

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from backend.app.marketplace.service.github_app_sync_service import github_app_sync_service
from backend.app.marketplace.service.github_sync_service import github_sync_service
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.core.conf import settings
from backend.database.db import async_db_session

router = APIRouter()


class WebhookResponse(BaseModel):
    """Webhook response"""
    message: str
    synced: int = 0
    failed: int = 0


def has_skill_source_changes(commits: list[dict]) -> bool:
    """Return true when push payload touches managed skill source roots."""
    for commit in commits:
        changed = commit.get('modified', []) + commit.get('added', []) + commit.get('removed', [])
        if any(
            path == '.gitmodules'
            or path == 'common-skills.yaml'
            or path.startswith('huanxing-skills/')
            or path == 'github'
            or path.startswith('github/')
            for path in changed
        ):
            return True
    return False


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


async def _run_skill_sync_background() -> None:
    """后台执行技能全量同步（自带 DB 会话）。

    webhook 接口验签+闸门后立即返回，繁重的 git pull + 全量重扫 + 逐技能翻译
    在这里跑——GitHub webhook 期望 ~10s 内 2xx，同步耗时长不能阻塞响应。
    """
    try:
        async with async_db_session() as db:
            result = await github_sync_service.sync_from_github(db, force=True)
        log.info(f"[Webhook] 后台技能同步完成: {result}")
    except Exception as exc:
        log.error(f"[Webhook] 后台技能同步失败: {exc}")


async def _run_template_sync_background() -> None:
    """后台执行模板全量同步（自带 DB 会话）。"""
    try:
        async with async_db_session() as db:
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
) -> ResponseSchemaModel[WebhookResponse]:
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

        if not has_skill_source_changes(payload.get('commits', [])):
            log.info("No skill changes detected, skipping sync")
            return response_base.success(data=WebhookResponse(
                message="No skill changes detected"
            ))

        # 异步触发：立即返回，繁重同步在后台跑
        background_tasks.add_task(_run_skill_sync_background)
        log.info("Queued background skill sync from GitHub webhook")
        return response_base.success(data=WebhookResponse(
            message="Skill sync queued (running in background)"
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
) -> ResponseSchemaModel[WebhookResponse]:
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
