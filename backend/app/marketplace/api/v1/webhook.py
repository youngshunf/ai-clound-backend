"""技能市场 GitHub Webhook API。"""
import hashlib
import hmac

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Header, Request
from pydantic import BaseModel

from backend.app.marketplace.service.github_sync_service import collect_changed_paths
from backend.common.log import log
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.core.conf import settings

router = APIRouter()


class WebhookResponse(BaseModel):
    """Webhook response"""
    message: str
    synced: int = 0
    failed: int = 0


def has_skill_source_changes(commits: list[dict]) -> bool:
    """判断推送是否要求在可信工作区执行 AstraHub 发布。"""
    return source_release_required(commits)


def source_release_required(commits: list[dict]) -> bool:
    """判断推送是否需要从可信本地仓库发布官方 Hub 制品。"""
    return any(
        path == '.gitmodules'
        or path in {'common-skills.yaml', 'common-bundles.yaml'}
        or path == 'github'
        or path.startswith(
            (
                'huanxing-skills/',
                'github/',
                'bundles/',
                'templates/',
                'workflow-templates/',
            )
        )
        for path in collect_changed_paths(commits)
    )


def bundle_source_changes(commits: list[dict]) -> set[str]:
    """兼容旧调用；服务器仓库同步已退役，始终返回空集。"""
    del commits
    return set()


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
    _background_tasks: BackgroundTasks,
    x_hub_signature_256: Annotated[str | None, Header(alias='X-Hub-Signature-256')] = None,
    x_github_event: Annotated[str | None, Header(alias='X-GitHub-Event')] = None,
) -> ResponseModel | ResponseSchemaModel[WebhookResponse]:
    """
    GitHub webhook endpoint for skills

    Triggered when hasn-hub repository receives a push event.
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
        if not release_required:
            log.info("No skill changes detected, skipping sync")
            return response_base.success(data=WebhookResponse(
                message="No skill changes detected"
            ))

        log.warning(
            "检测到官方 Hub 源码变更，服务器仓库扫描已退役；"
            "必须在可信 hasn-hub 工作区运行 astrahub publish all"
        )
        return response_base.success(data=WebhookResponse(
            message='Official Hub release required via astrahub publish all'
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
    _background_tasks: BackgroundTasks,
    x_hub_signature_256: Annotated[str | None, Header(alias='X-Hub-Signature-256')] = None,
    x_github_event: Annotated[str | None, Header(alias='X-GitHub-Event')] = None,
) -> ResponseModel | ResponseSchemaModel[WebhookResponse]:
    """
    GitHub webhook endpoint for templates

    Triggered when hasn-hub repository receives a push event.
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

        log.warning(
            '检测到模板源码变更，服务器仓库扫描已退役；'
            '必须在可信 hasn-hub 工作区运行 astrahub publish all'
        )
        return response_base.success(data=WebhookResponse(
            message='Official Hub release required via astrahub publish all'
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
