"""Explicit public JSON representations of immutable content snapshots."""
from ..core.json_values import thaw_json


def node_json(node):
    return {
        'id': node.id, 'kind': node.kind, 'name': node.name,
        'parent_id': node.parent_id, 'position': node.position,
        'version': node.version, 'path': node.path,
        'created_at': node.created_at, 'modified_at': node.modified_at,
        'metadata': thaw_json(node.metadata),
    }


def document_json(document):
    return {**node_json(document), 'content': document.content,
            'revision_id': document.revision_id}


def document_access_json(view):
    return {'document': document_json(view.document), 'access': content_access_json(view.access)}


def user_json(user):
    return {key: getattr(user, key) for key in ('id', 'login_name', 'display_name', 'status', 'site_admin', 'version')}


def session_json(session):
    return {'user': user_json(session.user), 'csrf': session.csrf_token, 'expires_at': session.expires_at}


def content_access_json(access):
    return {'version': access.version, 'actions': list(access.actions), 'visibility': access.visibility,
            'frozen': access.frozen, 'can_freeze': access.can_freeze, 'can_unfreeze': access.can_unfreeze}


def account_grant_json(grant):
    return {'user': user_json(grant.user), 'token': grant.token,
            'purpose': grant.purpose, 'expires_at': grant.expires_at}


def workspace_access_json(access):
    return {'version': access.version, 'read_scope': access.read_scope,
            'members': [{'user': user_json(member.user), 'role': member.role,
                         'status': member.status} for member in access.members]}
