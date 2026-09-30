"""Field validation and calls into the fixed-scope content service."""
from .serialization import node_json, document_json


class RequestError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def fields(value, required=(), optional=()):
    if not isinstance(value, dict):
        raise RequestError(400, 'invalid_request', 'Expected a JSON object.')
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        raise RequestError(400, 'invalid_request', 'Missing or unknown request fields.')
    if any(not isinstance(item, str) for item in value.values()):
        raise RequestError(400, 'invalid_request', 'Request fields must be strings.')
    return value


class API:
    def __init__(self, service, scope, nonce):
        self.service, self.scope, self.nonce = service, scope, nonce

    def dispatch(self, method, path, query, body=None):
        service, scope = self.service, self.scope
        if method == 'GET' and path == '/api/bootstrap':
            fields(query)
            return 200, {'root': node_json(service.get_node(scope, scope.root_id)), 'nonce': self.nonce}
        if method == 'GET' and path == '/api/children':
            fields(query, ('folder_id',))
            return 200, {'nodes': [node_json(node) for node in service.list_children(scope, query['folder_id'])]}
        if method == 'GET' and path == '/api/document':
            fields(query, ('object_id',))
            return 200, {'document': document_json(service.read_document(scope, query['object_id']))}
        if method == 'POST' and path == '/api/folder':
            fields(query)
            fields(body, ('parent_id', 'name'))
            return 201, {'node': node_json(service.create_folder(scope, body['parent_id'], body['name']))}
        if method == 'POST' and path == '/api/document':
            fields(query)
            fields(body, ('parent_id', 'name'), ('content',))
            return 201, {'document': document_json(service.create_document(scope, body['parent_id'], body['name'], content=body.get('content', '')))}
        if method == 'PUT' and path == '/api/document':
            fields(query)
            fields(body, ('object_id', 'content', 'expected_revision_id'))
            return 200, {'document': document_json(service.save_document(scope, body['object_id'], body['content'], expected_revision_id=body['expected_revision_id']))}
        if path in ('/api/bootstrap', '/api/children', '/api/document', '/api/folder'):
            raise RequestError(405, 'method_not_allowed', 'Method not allowed.')
        raise RequestError(404, 'not_found', 'Route not found.')
