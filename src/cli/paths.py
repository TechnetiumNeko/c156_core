"""Session-local virtual paths, resolved exclusively by ContentService."""

from ..core.errors import ContentError, NotFound, PathOutsideRoot
from ..core.models import ContentScope, NodeSnapshot
from ..services.content import ContentService


class VirtualFileSystem:
    def __init__(self, service: ContentService, scope: ContentScope):
        self.service = service
        self.scope = scope
        self.cwd_id = scope.root_id

    def resolve(self, value: str = '.') -> NodeSnapshot:
        return self.service.resolve_path(self.scope, value, cwd_id=self.cwd_id)

    def display(self, object_id: str | None = None) -> str:
        return self.service.get_path(self.scope, self.cwd_id if object_id is None else object_id)

    def ensure_cwd(self) -> bool:
        try:
            self.service.get_node(self.scope, self.cwd_id)
        except (NotFound, PathOutsideRoot):
            self.cwd_id = self.scope.root_id
            return True
        return False

    def completion_candidates(self, value: str, directories_only: bool = False) -> list[str]:
        if value == '~':
            parent_text, render_prefix, leaf = '~', '~/', ''
        elif '/' in value:
            parent_text, leaf = value.rsplit('/', 1)
            parent_text = parent_text or '/'
            render_prefix = value[:len(value) - len(leaf)]
        else:
            parent_text, render_prefix, leaf = '.', '', value
        try:
            parent = self.resolve(parent_text)
            entries = self.service.list_children(self.scope, parent.id)
        except ContentError:
            return []
        entries.sort(key=lambda node: (node.kind != 'folder', node.name.lower()))
        return [render_prefix + node.name + ('/' if node.kind == 'folder' else '')
                for node in entries if node.name.startswith(leaf)
                and (not directories_only or node.kind == 'folder')]
