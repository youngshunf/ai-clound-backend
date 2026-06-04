"""
GitHub Webhook API for Marketplace

Receives GitHub push events and triggers sync for skills and templates.
"""
import hashlib
import hmac

from typing import Annotated

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel

from backend.app.marketplace.service.github_app_sync_service import github_app_sync_service
from backend.app.marketplace.service.github_sync_service import github_sync_service
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.core.conf import settings
from backend.database.db import CurrentSessionTransaction

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


@router.post(
    '/github/skills',
    summary='GitHub Webhook for Skills',
    description='Receives GitHub push events and triggers skill sync',
)
async def github_webhook_skills(
    request: Request,
    db: CurrentSessionTransaction,
    x_hub_signature_256: Annotated[str | None, Header(alias='X-Hub-Signature-256')] = None,
    x_github_event: Annotated[str | None, Header(alias='X-GitHub-Event')] = None,
) -> ResponseSchemaModel[WebhookResponse]:
    """
    GitHub webhook endpoint for skills

    Triggered when huanxing-hub repository receives a push event
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

        # Trigger sync
        log.info("Triggering skill sync from GitHub webhook")
        result = await github_sync_service.sync_from_github(db, force=True)

        if result.get('success'):
            return response_base.success(data=WebhookResponse(
                message="Skill sync completed",
                synced=result.get('synced', 0),
                failed=result.get('failed', 0)
            ))
        from backend.common.response.response_code import CustomResponse
        return response_base.fail(
            res=CustomResponse(code=500, msg=f"Skill sync failed: {result.get('error')}"),
            data=WebhookResponse(message="Sync failed", synced=0, failed=0)
        )

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
    db: CurrentSessionTransaction,
    x_hub_signature_256: Annotated[str | None, Header(alias='X-Hub-Signature-256')] = None,
    x_github_event: Annotated[str | None, Header(alias='X-GitHub-Event')] = None,
) -> ResponseSchemaModel[WebhookResponse]:
    """
    GitHub webhook endpoint for templates

    Triggered when huanxing-hub repository receives a push event
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

        # Trigger sync
        log.info("Triggering template sync from GitHub webhook")
        result = await github_app_sync_service.sync_from_github(db, force=True)

        if result.get('success'):
            return response_base.success(data=WebhookResponse(
                message="Template sync completed",
                synced=result.get('synced', 0),
                failed=result.get('failed', 0)
            ))
        from backend.common.response.response_code import CustomResponse
        return response_base.fail(
            res=CustomResponse(code=500, msg=f"Template sync failed: {result.get('error')}"),
            data=WebhookResponse(message="Sync failed", synced=0, failed=0)
        )

    except Exception as e:
        log.error(f"GitHub webhook error: {e}")
        from backend.common.response.response_code import CustomResponse
        return response_base.fail(
            res=CustomResponse(code=500, msg=str(e)),
            data=WebhookResponse(message="Webhook error", synced=0, failed=0)
        )
