"""Explicit typed request schemas and public application-service routing."""
from ..access.models import AccessRule
from ..identity.models import AccountTokenGrant
from .auth import APIResponse
from .serialization import (node_json, document_json, user_json, session_json,
                            account_grant_json, content_access_json,
                            workspace_access_json, object_access_json)


class RequestError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def fields(value, required=(), optional=(), *, types=None):
    if not isinstance(value, dict):
        raise RequestError(400, 'invalid_request', 'Expected a JSON object.')
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        raise RequestError(400, 'invalid_request', 'Missing or unknown request fields.')
    types = types or {}
    if any(type(item) is not types.get(key, str) for key, item in value.items()):
        raise RequestError(400, 'invalid_request', 'Invalid request field type.')
    return value


class API:
    def __init__(self, service, scope, nonce, *, identity_service, accounts, access):
        self.service, self.scope, self.nonce = service, scope, nonce
        self.identity, self.accounts, self.access = identity_service, accounts, access

    def dispatch(self, method, path, query, body=None, *, identity):
        service, scope, token = self.service, self.scope, identity.session_token
        auth = {'session_token': token}
        if method == 'GET' and path == '/api/bootstrap':
            fields(query)
            view = service.bootstrap(**auth)
            if view.session is None:
                return APIResponse(200, {'initialized': view.initialized, 'nonce': self.nonce})
            root = view.root
            return APIResponse(200, {'initialized': view.initialized, **session_json(view.session),
                'workspace_access_version': view.workspace_access_version, 'workspace_role': view.workspace_role,
                'root': node_json(root.node) if root else None,
                'root_access': content_access_json(root.access) if root else None})
        if method == 'GET' and path == '/api/session':
            fields(query)
            return APIResponse(200, session_json(self.identity.current_session(**auth)))
        if method == 'GET' and path == '/api/children':
            fields(query, ('folder_id',))
            views = service.list_children_with_access(scope, query['folder_id'], **auth)
            return APIResponse(200, {'nodes': [{**node_json(v.node), 'access': content_access_json(v.access)} for v in views]})
        if method == 'GET' and path == '/api/document':
            fields(query, ('object_id',))
            view = service.read_document_with_access(scope, query['object_id'], **auth)
            return APIResponse(200, {'document': document_json(view.document), 'access': content_access_json(view.access)})
        if method == 'GET' and path == '/api/admin/users':
            fields(query)
            return APIResponse(200, {'users': [user_json(u) for u in self.accounts.list_users(**auth)]})
        if method == 'GET' and path == '/api/workspace/members':
            fields(query)
            return APIResponse(200, {'workspace': workspace_access_json(self.access.workspace_access(scope, **auth))})
        if method == 'GET' and path == '/api/access':
            fields(query, ('object_id',))
            return APIResponse(200, {'access': object_access_json(self.access.object_access(scope, query['object_id'], **auth))})
        fields(query)
        schemas = {
            ('POST', '/api/auth/login'): (('login_name', 'password'), ()),
            ('POST', '/api/auth/activate'): (('token', 'password'), ()),
            ('POST', '/api/auth/reset'): (('token', 'password'), ()),
            ('POST', '/api/auth/logout'): ((), ()),
            ('PUT', '/api/account/password'): (('old_password', 'new_password'), ()),
            ('PUT', '/api/account/profile'): (('display_name', 'expected_version'), ()),
            ('POST', '/api/admin/users'): (('login_name', 'display_name'), ()),
            **{('POST', '/api/admin/users/' + action): (('user_id', 'expected_version'), ()) for action in ('activation', 'reset', 'disable', 'enable')},
            ('PUT', '/api/admin/users/site-admin'): (('user_id', 'enabled', 'expected_version'), ()),
            ('POST', '/api/workspace/members'): (('login_name', 'role', 'expected_version'), ()),
            ('PUT', '/api/workspace/members'): (('user_id', 'role', 'expected_version'), ()),
            ('DELETE', '/api/workspace/members'): (('user_id', 'expected_version'), ()),
            ('POST', '/api/workspace/ownership'): (('target_user_id', 'expected_version'), ()),
            ('PUT', '/api/workspace/read-scope'): (('read_scope', 'expected_version'), ()),
            ('PUT', '/api/access/rule'): (('object_id', 'subject_type', 'subject_key', 'action', 'effect', 'expected_version'), ()),
            ('DELETE', '/api/access/rule'): (('object_id', 'subject_type', 'subject_key', 'action', 'expected_version'), ()),
            ('PUT', '/api/access/visibility'): (('object_id', 'visibility', 'expected_version'), ()),
            ('POST', '/api/document/freeze'): (('object_id', 'expected_version'), ()),
            ('DELETE', '/api/document/freeze'): (('object_id', 'expected_version'), ()),
            ('POST', '/api/folder'): (('parent_id', 'name'), ('visibility',)),
            ('POST', '/api/document'): (('parent_id', 'name'), ('content', 'visibility')),
            ('PUT', '/api/document'): (('object_id', 'content', 'expected_revision_id'), ()),
        }
        schema = schemas.get((method, path))
        if schema is None:
            known = {p for _, p in schemas} | {'/api/bootstrap', '/api/session', '/api/children', '/api/access'}
            raise RequestError(405 if path in known else 404, 'method_not_allowed' if path in known else 'not_found', 'Method not allowed.' if path in known else 'Route not found.')
        fields(body, *schema, types={'expected_version': int, 'enabled': bool})
        if path == '/api/auth/login':
            grant = self.identity.login(**body, source=identity.source)
            return APIResponse(200, session_json(grant), session_grant=grant)
        if path in ('/api/auth/activate', '/api/auth/reset'):
            call = self.identity.activate if path.endswith('activate') else self.identity.reset_password
            return APIResponse(200, {'user': user_json(call(**body, source=identity.source))})
        if path == '/api/auth/logout':
            self.identity.logout(**auth)
            return APIResponse(200, {'ok': True}, clear_cookie=True)
        if path == '/api/account/password':
            self.identity.change_password(**body, **auth, source=identity.source)
            return APIResponse(200, {'ok': True}, clear_cookie=True)
        if path == '/api/account/profile':
            return APIResponse(200, {'user': user_json(self.identity.change_display_name(**body, **auth))})
        if path == '/api/admin/users':
            return APIResponse(201, account_grant_json(self.accounts.create_user(**body, **auth)))
        if path.startswith('/api/admin/users/'):
            action = path.rsplit('/', 1)[1]
            call = {'activation': self.accounts.resend_activation, 'reset': self.accounts.reset_user,
                    'disable': self.accounts.disable_user, 'enable': self.accounts.enable_user,
                    'site-admin': self.accounts.set_site_admin}[action]
            result = call(**body, **auth)
            return APIResponse(200, account_grant_json(result) if isinstance(result, AccountTokenGrant) else {'user': user_json(result)})
        if path == '/api/workspace/members':
            call = {'POST': self.access.add_member, 'PUT': self.access.set_member_role, 'DELETE': self.access.remove_member}[method]
            return APIResponse(200, {'workspace': workspace_access_json(call(scope, **body, **auth))})
        if path in ('/api/workspace/ownership', '/api/workspace/read-scope'):
            call = self.access.transfer_ownership if path.endswith('ownership') else self.access.set_read_scope
            return APIResponse(200, {'workspace': workspace_access_json(call(scope, **body, **auth))})
        if path == '/api/access/rule':
            rule = AccessRule(**{key: body[key] for key in ('object_id', 'subject_type', 'subject_key', 'action')}, effect=body.get('effect', 'deny'))
            call = self.access.put_rule if method == 'PUT' else self.access.remove_rule
            return APIResponse(200, {'access': object_access_json(call(scope, rule, **auth, expected_version=body['expected_version']))})
        if path == '/api/access/visibility':
            return APIResponse(200, {'access': object_access_json(self.access.set_visibility(scope, **body, **auth))})
        if path == '/api/document/freeze':
            call = self.access.freeze_document if method == 'POST' else self.access.unfreeze_document
            return APIResponse(200, {'access': content_access_json(call(scope, **body, **auth))})
        if path == '/api/folder':
            view = service.create_folder_with_access(scope, **body, **auth)
            return APIResponse(201, {'node': node_json(view.node), 'access': content_access_json(view.access)})
        if method == 'POST':
            view = service.create_document_with_access(scope, **body, **auth)
            return APIResponse(201, {'document': document_json(view.document), 'access': content_access_json(view.access)})
        view = service.save_document_with_access(scope, **body, **auth)
        return APIResponse(200, {'document': document_json(view.document), 'access': content_access_json(view.access)})
