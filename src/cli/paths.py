"""Session-local virtual paths, resolved exclusively by ContentService."""

from ..core.errors import ContentError, NotFound, PathOutsideRoot
from ..core.models import ContentScope, NodeSnapshot
from ..services.content import ContentService


class VirtualFileSystem:
    def __init__(self, service: ContentService, scope: ContentScope, *, session_token: str | None):
        self.service = service
        self.scope = scope
        self.cwd_id = scope.root_id
        self.session_token = session_token
        self.available = True
        self.cached_path = "/"
        self.ancestors = [scope.root_id]

    def resolve(self, value: str = '.') -> NodeSnapshot:
        return self.service.resolve_path(self.scope, value, cwd_id=self.cwd_id, session_token=self.session_token)

    def display(self, object_id: str | None = None) -> str:
        return self.service.get_path(self.scope, self.cwd_id if object_id is None else object_id, session_token=self.session_token)

    def ensure_cwd(self) -> bool:
        previous = self.cwd_id
        candidates = list(dict.fromkeys([self.cwd_id, *self.ancestors, self.scope.root_id]))
        self.available = False
        for candidate in candidates:
            try:
                node = self.service.get_node(self.scope, candidate, session_token=self.session_token)
            except (NotFound, PathOutsideRoot):
                continue
            self.cwd_id = candidate
            self.cached_path = node.path
            self.available = True
            chain = [candidate]
            while node.parent_id is not None:
                node = self.service.get_node(self.scope, node.parent_id, session_token=self.session_token)
                chain.append(node.id)
            self.ancestors = chain
            return previous != candidate
        self.cached_path = "无可用内容"
        return False

    def completion_candidates(self, value: str, directories_only: bool = False) -> list[str]:
        if self.session_token is None:
            return []
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
            entries = self.service.list_children(self.scope, parent.id, session_token=self.session_token)
        except ContentError:
            return []
        entries.sort(key=lambda node: (node.kind != 'folder', node.name.lower()))
        return [render_prefix + node.name + ('/' if node.kind == 'folder' else '')
                for node in entries if node.name.startswith(leaf)
                and (not directories_only or node.kind == 'folder')]
