"""Content structure adapters use existing version and subtree checks."""
from fastapi import APIRouter, Depends, Request
from .auth import parse_identity
from .dependencies import body, write_identity
from . import schemas as s
from .serialization import node_json, document_access_json, content_access_json
from .transport import read_query

router = APIRouter()


@router.post('/api/folder', status_code=201)
def create_folder(request: Request, value=Depends(body(s.CreateFolderBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    view = services.content.create_folder_with_access(services.scope, **value.model_dump(), session_token=actor.session_token)
    return {'node': node_json(view.node), 'access': content_access_json(view.access)}


@router.post('/api/document', status_code=201)
def create_document(request: Request, value=Depends(body(s.CreateDocumentBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    return document_access_json(services.content.create_document_with_access(services.scope, **value.model_dump(), session_token=actor.session_token))


@router.put('/api/node/rename')
def rename(request: Request, value=Depends(body(s.RenameBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    return {'node': node_json(services.content.rename_node(services.scope, **value.model_dump(), session_token=actor.session_token))}


@router.get('/api/folder/delete-plan')
def delete_plan(request: Request):
    query = read_query(request, required=('folder_id',))
    services = request.app.state.services
    plan = services.content.prepare_delete(services.scope, query['folder_id'], session_token=parse_identity(request).session_token)
    return {'object_id': plan.object_id, 'version': plan.version, 'subtree_token': plan.subtree_token,
            'items': [{'node': node_json(item.node), 'depth': item.depth} for item in plan.items]}


@router.delete('/api/node')
def delete(request: Request, value=Depends(body(s.DeleteBody)), actor=Depends(write_identity)):
    services = request.app.state.services
    services.content.delete_node(services.scope, **value.model_dump(), session_token=actor.session_token)
    return {'ok': True}
