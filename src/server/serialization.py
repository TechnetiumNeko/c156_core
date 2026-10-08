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


def operation_result_json(result):
    if result is None:
        return {'operation': None}
    receipt = result.operation
    return {'operation': {key: getattr(receipt, key) for key in (
        'operation_id', 'operation_type', 'result_revision_id', 'changed', 'created_at')},
        'current_revision_id': result.current_revision_id}


def revision_json(revision):
    return {key: getattr(revision, key) for key in (
        'revision_id', 'parent_revision_id', 'actor_id', 'actor_display_name',
        'source_kind', 'restored_from_revision_id', 'created_at')}


def revision_page_json(page):
    return {'revisions': [revision_json(revision) for revision in page.revisions],
            'head_revision_id': page.head_revision_id, 'next_cursor': page.next_cursor}


def revision_view_json(revision):
    return {**revision_json(revision), 'content': revision.content}


def revision_diff_json(diff):
    return {'from_revision_id': diff.from_revision_id, 'to_revision_id': diff.to_revision_id,
            'diff': diff.diff}


def deleted_page_json(page):
    return {'documents': [{'object_id': doc.object_id, 'name': doc.name, 'path': doc.path}
                          for doc in page.documents], 'next_cursor': page.next_cursor}
