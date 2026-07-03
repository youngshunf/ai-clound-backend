from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.app.hasn.service.hasn_auth import (
    hasn_auth_from_node_credential,
    verify_owner_proof,
)
from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSession

router = APIRouter(prefix='/node', tags=['HASN Node 控制平面'])


class OwnerProofPayload(BaseModel):
    type: str = Field(description='owner_proof 类型: owner_api_key | bearer_token')
    credential: str = Field(description='Owner 证明凭据')


class AddOwnerReq(BaseModel):
    owner_id: str = Field(description='Owner 的 hasn_id')
    owner_proof: OwnerProofPayload


class RenewOwnerReq(BaseModel):
    owner_proof: OwnerProofPayload


@router.post('/owners', summary='绑定 Owner 到当前 Node')
async def add_owner_to_node(
    obj_in: AddOwnerReq,
    db: CurrentSession,
    node_auth: Annotated[dict, Depends(hasn_auth_from_node_credential)],
) -> ResponseModel:
    proof = await verify_owner_proof(
        owner_id=obj_in.owner_id,
        owner_proof=obj_in.owner_proof.model_dump(),
        node_id=node_auth['node_id'],
        db=db,
    )
    binding = await hasn_node_bindings_service.add_owner_binding(
        db=db,
        node_id=node_auth['node_id'],
        owner_id=obj_in.owner_id,
        auth_profile=proof['auth_profile'],
        scopes=proof['scopes'],
        expires_at=proof['expires_at'],
    )
    await db.commit()
    return response_base.success(data={
        'binding_id': binding.binding_id,
        'owner_id': binding.owner_id,
        'accepted': True,
        'scopes': binding.scopes,
        'expires_at': binding.expires_at,
    })


@router.get('/owners', summary='查询当前 Node 已绑定 Owner')
async def list_node_owners(
    db: CurrentSession,
    node_auth: Annotated[dict, Depends(hasn_auth_from_node_credential)],
) -> ResponseModel:
    bindings = await hasn_node_bindings_service.list_active_bindings(
        db=db,
        node_id=node_auth['node_id'],
    )
    return response_base.success(data={
        'owners': [
            {
                'binding_id': b.binding_id,
                'owner_id': b.owner_id,
                'status': b.status,
                'expires_at': b.expires_at,
            }
            for b in bindings
        ]
    })


@router.post('/owners/{owner_id}/renew', summary='续期当前 Node 的 Owner Binding')
async def renew_node_owner(
    owner_id: str,
    obj_in: RenewOwnerReq,
    db: CurrentSession,
    node_auth: Annotated[dict, Depends(hasn_auth_from_node_credential)],
) -> ResponseModel:
    proof = await verify_owner_proof(
        owner_id=owner_id,
        owner_proof=obj_in.owner_proof.model_dump(),
        node_id=node_auth['node_id'],
        db=db,
    )
    binding = await hasn_node_bindings_service.renew_owner_binding(
        db=db,
        node_id=node_auth['node_id'],
        owner_id=owner_id,
        expires_at=proof['expires_at'],
    )
    await db.commit()
    return response_base.success(data={
        'binding_id': binding.binding_id,
        'owner_id': binding.owner_id,
        'accepted': True,
        'expires_at': binding.expires_at,
    })


@router.delete('/owners/{owner_id}', summary='解绑当前 Node 的 Owner')
async def remove_node_owner(
    owner_id: str,
    db: CurrentSession,
    node_auth: Annotated[dict, Depends(hasn_auth_from_node_credential)],
) -> ResponseModel:
    removed = await hasn_node_bindings_service.remove_owner_binding(
        db=db,
        node_id=node_auth['node_id'],
        owner_id=owner_id,
    )
    await db.commit()
    return response_base.success(data={'owner_id': owner_id, 'accepted': removed})
