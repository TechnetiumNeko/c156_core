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
