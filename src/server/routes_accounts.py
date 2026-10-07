"""Account HTTP adapters; target IDs never determine the current identity."""
from fastapi import APIRouter, Depends, Request, Response
from .auth import parse_identity, require_login_nonce
from .dependencies import body, write_identity
from . import schemas as s
from .serialization import user_json, account_grant_json
from .transport import read_query

router = APIRouter()


def clear_cookie(request, response):
    response.delete_cookie('c156_session', path='/', httponly=True, samesite='strict',
                           secure=request.app.state.config.cookie_secure)


@router.post('/api/auth/activate')
def activate(request: Request, value=Depends(body(s.TokenPasswordBody))):
    require_login_nonce(request, request.app.state.services)
    actor = parse_identity(request)
    user = request.app.state.services.identity.activate(**value.model_dump(), source=actor.source)
    return {'user': user_json(user)}


@router.post('/api/auth/reset')
def reset(request: Request, value=Depends(body(s.TokenPasswordBody))):
    require_login_nonce(request, request.app.state.services)
    actor = parse_identity(request)
    user = request.app.state.services.identity.reset_password(**value.model_dump(), source=actor.source)
    return {'user': user_json(user)}


@router.put('/api/account/profile')
def profile(request: Request, value=Depends(body(s.ProfileBody)), actor=Depends(write_identity)):
    user = request.app.state.services.identity.change_display_name(**value.model_dump(), session_token=actor.session_token)
    return {'user': user_json(user)}


@router.put('/api/account/password')
def password(request: Request, response: Response, value=Depends(body(s.PasswordBody)), actor=Depends(write_identity)):
    request.app.state.services.identity.change_password(**value.model_dump(), session_token=actor.session_token, source=actor.source)
    clear_cookie(request, response)
    return {'ok': True}


@router.get('/api/admin/users')
def users(request: Request):
    read_query(request)
    actor = parse_identity(request)
    return {'users': [user_json(user) for user in request.app.state.services.accounts.list_users(session_token=actor.session_token)]}


@router.post('/api/admin/users', status_code=201)
def create_user(request: Request, value=Depends(body(s.CreateUserBody)), actor=Depends(write_identity)):
    return account_grant_json(request.app.state.services.accounts.create_user(**value.model_dump(), session_token=actor.session_token))


def user_action(method, credential=False):
    def call(request: Request, response: Response, value=Depends(body(s.UserTargetBody)), actor=Depends(write_identity)):
        result = method(request.app.state.services.accounts, **value.model_dump(), session_token=actor.session_token)
        return account_grant_json(result) if credential else {'user': user_json(result)}
    return call


from ..services.accounts import AccountService
for action, method, credential in (
    ('activation', AccountService.resend_activation, True),
    ('reset', AccountService.reset_user, True),
    ('disable', AccountService.disable_user, False),
    ('enable', AccountService.enable_user, False),
):
    router.add_api_route('/api/admin/users/' + action, user_action(method, credential), methods=['POST'])


@router.put('/api/admin/users/site-admin')
def site_admin(request: Request, value=Depends(body(s.SiteAdminBody)), actor=Depends(write_identity)):
    return {'user': user_json(request.app.state.services.accounts.set_site_admin(**value.model_dump(), session_token=actor.session_token))}
