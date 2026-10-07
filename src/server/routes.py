"""Synchronous routes directly adapt existing application services."""
from fastapi import APIRouter, Depends, Request, Response
from .auth import parse_identity, require_login_nonce, require_session_csrf, set_session_cookie
from .schemas import LoginBody, EmptyBody, SaveDocumentBody, validate_body
from .serialization import session_json, node_json, content_access_json, document_access_json
from .transport import read_json_object, read_query

router = APIRouter()

@router.get('/api/healthz')
def readiness(request: Request):
    from ..storage import Database
    from ..storage.errors import StorageError
    from ..core.errors import ContentError
    from ..services.unit_of_work import ApplicationUnitOfWork
    from .errors import RequestError
    config = request.app.state.config
    try:
        with ApplicationUnitOfWork(Database(config.database_path, busy_timeout_ms=250)).transaction() as work:
            work.default_scope()
    except (StorageError, ContentError, OSError, ValueError):
        raise RequestError(503, 'unavailable', 'Service unavailable.') from None
    return {'status': 'ok', 'build_sha': config.build_sha}


async def login_body(request: Request):
    read_query(request)
    return validate_body(LoginBody, await read_json_object(request))

async def empty_body(request: Request):
    read_query(request)
    return validate_body(EmptyBody, await read_json_object(request))

async def save_document_body(request: Request):
    read_query(request)
    return validate_body(SaveDocumentBody, await read_json_object(request))

@router.get('/api/children')
def children(request: Request):
    query = read_query(request, required=('folder_id',))
    identity = parse_identity(request)
    services = request.app.state.services
    views = services.content.list_children_with_access(
        services.scope, query['folder_id'], session_token=identity.session_token)
    return {'nodes': [{**node_json(view.node), 'access': content_access_json(view.access)}
                      for view in views]}

@router.get('/api/document')
def read_document(request: Request):
    query = read_query(request, required=('object_id',))
    identity = parse_identity(request)
    services = request.app.state.services
    return document_access_json(services.content.read_document_with_access(
        services.scope, query['object_id'], session_token=identity.session_token))

@router.put('/api/document')
def save_document(request: Request, body: SaveDocumentBody = Depends(save_document_body)):
    identity = parse_identity(request)
    services = request.app.state.services
    require_session_csrf(request, services, identity)
    return document_access_json(services.content.save_document_with_access(
        services.scope, body.object_id, body.content,
        expected_revision_id=body.expected_revision_id, session_token=identity.session_token))

@router.get('/api/bootstrap')
def bootstrap(request: Request):
    read_query(request)
    identity = parse_identity(request)
    services = request.app.state.services
    view = services.content.bootstrap(session_token=identity.session_token)
    if view.session is None:
        return {'initialized': view.initialized, 'nonce': services.nonce}
    root = view.root
    return {'initialized': view.initialized, **session_json(view.session),
            'workspace_access_version': view.workspace_access_version, 'workspace_role': view.workspace_role,
            'root': node_json(root.node) if root else None,
            'root_access': content_access_json(root.access) if root else None}

@router.get('/api/session')
def session(request: Request):
    read_query(request)
    identity = parse_identity(request)
    return session_json(request.app.state.services.identity.current_session(session_token=identity.session_token))

@router.post('/api/auth/login')
def login(request: Request, response: Response, body: LoginBody = Depends(login_body)):
    services = request.app.state.services
    require_login_nonce(request, services)
    identity = parse_identity(request)
    grant = services.identity.login(body.login_name, body.password, source=identity.source)
    set_session_cookie(response, grant, secure=request.app.state.config.cookie_secure)
    return session_json(grant)

@router.post('/api/auth/logout')
def logout(request: Request, response: Response, body: EmptyBody = Depends(empty_body)):
    services = request.app.state.services
    identity = parse_identity(request)
    require_session_csrf(request, services, identity)
    services.identity.logout(session_token=identity.session_token)
    response.delete_cookie('c156_session', path='/', httponly=True, samesite='strict', secure=request.app.state.config.cookie_secure)
    return {'ok': True}
