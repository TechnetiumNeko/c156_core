"""Strict HTTP adapters for history and body-free operation confirmation."""
import re
from fastapi import APIRouter, Depends, Request
from .auth import parse_identity, require_session_csrf
from .errors import RequestError
from .schemas import RestoreDocumentBody, validate_body
from .serialization import (operation_result_json, revision_page_json, revision_view_json,
                            revision_diff_json, deleted_page_json)
from .transport import read_json_object, read_query

router = APIRouter()


def page_query(request, *, required=()):
    # Delegate duplicates/unknown fields to the same strict transport reader.
    present = set(request.query_params)
    query = read_query(request, required=(*required, *(name for name in ('cursor', 'limit')
                                                     if name in present)))
    options = {}
    if 'cursor' in query:
        options['cursor'] = query.pop('cursor')
    if 'limit' in query:
        value = query.pop('limit')
        if not re.fullmatch(r'[1-9][0-9]{0,2}', value) or int(value) > 100:
            raise RequestError(400, 'invalid_request', 'Invalid page limit.')
        options['limit'] = int(value)
    return query, options


@router.get('/api/document/history')
def history(request: Request):
    query, options = page_query(request, required=('object_id',))
    services = request.app.state.services
    identity = parse_identity(request)
    return revision_page_json(services.content.list_document_revisions(
        services.scope, query['object_id'], **options, session_token=identity.session_token))


@router.get('/api/document/revision')
def revision(request: Request):
    query = read_query(request, required=('object_id', 'revision_id'))
    services = request.app.state.services
    identity = parse_identity(request)
    return revision_view_json(services.content.read_document_revision(
        services.scope, query['object_id'], query['revision_id'], session_token=identity.session_token))


@router.get('/api/document/diff')
def diff(request: Request):
    query = read_query(request, required=('object_id', 'from_revision_id', 'to_revision_id'))
    services = request.app.state.services
    identity = parse_identity(request)
    return revision_diff_json(services.content.compare_document_revisions(
        services.scope, query['object_id'], query['from_revision_id'], query['to_revision_id'],
        session_token=identity.session_token))


@router.get('/api/documents/deleted')
def deleted(request: Request):
    _, options = page_query(request)
    services = request.app.state.services
    identity = parse_identity(request)
    return deleted_page_json(services.content.list_deleted_documents(
        services.scope, **options, session_token=identity.session_token))


@router.get('/api/document/operation')
def operation(request: Request):
    query = read_query(request, required=('object_id', 'operation_id'))
    services = request.app.state.services
    identity = parse_identity(request)
    return operation_result_json(services.content.get_operation_status(
        services.scope, query['object_id'], query['operation_id'], session_token=identity.session_token))


async def restore_body(request: Request):
    read_query(request)
    return validate_body(RestoreDocumentBody, await read_json_object(request))


@router.post('/api/document/restore')
def restore(request: Request, body: RestoreDocumentBody = Depends(restore_body)):
    services = request.app.state.services
    identity = parse_identity(request)
    require_session_csrf(request, services, identity)
    return operation_result_json(services.content.restore_document_revision(
        services.scope, body.object_id, body.source_revision_id,
        expected_revision_id=body.expected_revision_id, operation_id=body.operation_id,
        session_token=identity.session_token))
