"""Workspace membership remains independent from site account administration."""
from fastapi import APIRouter, Depends, Request
from .auth import parse_identity
from .dependencies import body, write_identity
from . import schemas as s
from .serialization import workspace_access_json
from .transport import read_query

router = APIRouter()


@router.get('/api/workspace/members')
def members(request: Request):
    read_query(request)
    services = request.app.state.services
    return {'workspace': workspace_access_json(services.access.workspace_access(services.scope, session_token=parse_identity(request).session_token))}


@router.post('/api/workspace/members')
def add_member(request: Request, value=Depends(body(s.AddMemberBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    return {'workspace': workspace_access_json(services.access.add_member(services.scope, **value.model_dump(), session_token=actor.session_token))}


@router.put('/api/workspace/members')
def set_role(request: Request, value=Depends(body(s.MemberRoleBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    return {'workspace': workspace_access_json(services.access.set_member_role(services.scope, **value.model_dump(), session_token=actor.session_token))}


@router.delete('/api/workspace/members')
def remove_member(request: Request, value=Depends(body(s.UserTargetBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    return {'workspace': workspace_access_json(services.access.remove_member(services.scope, **value.model_dump(), session_token=actor.session_token))}


@router.put('/api/workspace/read-scope')
def set_read_scope(request: Request, value=Depends(body(s.ReadScopeBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    return {'workspace': workspace_access_json(services.access.set_read_scope(services.scope, **value.model_dump(), session_token=actor.session_token))}


@router.post('/api/workspace/ownership')
def transfer_ownership(request: Request, value=Depends(body(s.OwnershipBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    return {'workspace': workspace_access_json(services.access.transfer_ownership(services.scope, **value.model_dump(), session_token=actor.session_token))}


@router.post('/api/workspace/invitations', status_code=201)
def invite_member(request: Request, value=Depends(body(s.InviteMemberBody)), actor=Depends(write_identity)):
    from .serialization import account_grant_json
    services = request.app.state.services
    result = services.invitations.invite(services.scope, **value.model_dump(), session_token=actor.session_token)
    return {**account_grant_json(result.grant), 'workspace': workspace_access_json(result.workspace)}
