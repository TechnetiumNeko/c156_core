import type { Bootstrap, Session, ChildrenResponse, DocumentResponse, SaveDocument, UserResponse, AccountGrant, WorkspaceResponse, NodeResponse, DeletePlan, UserAction, SaveDocumentOperation, RestoreDocumentOperation, OperationResult, OperationStatus, RevisionPage, RevisionView, RevisionDiff, DeletedDocumentPage } from './types.ts';
export class ApiError extends Error {
  code: string; status: number;
  get outcome(): 'rejected' | 'uncertain' {
    return ['unauthenticated', 'forbidden', 'frozen', 'not_found', 'conflict', 'already_exists',
      'not_directory', 'not_document', 'invalid_name', 'invalid_argument', 'path_outside_root',
      'directory_not_empty', 'protected_node', 'invalid_move', 'storage_busy', 'unsupported_schema',
      'rate_limited', 'invalid_request', 'payload_too_large', 'method_not_allowed'].includes(this.code)
      ? 'rejected' : 'uncertain';
  }
  constructor(code: string, message: string, status = 0) { super(message); this.name = 'ApiError'; this.code = code; this.status = status; }
}
type RecordValue = Record<string, unknown>;
const record = (v: unknown): v is RecordValue => typeof v === 'object' && v !== null && !Array.isArray(v);
const string = (v: unknown): v is string => typeof v === 'string';
const id = (v: unknown): v is string => string(v) && v.length > 0;
const nullableId = (v: unknown) => v === null || id(v);
const timestamp = (v: unknown) => string(v) && /^\d{4}-\d{2}-\d{2}T/.test(v) && Number.isFinite(Date.parse(v));
const exact = (v: RecordValue, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => k in v);
const receipt = (v: unknown) => record(v) && exact(v, ['operation_id', 'operation_type', 'result_revision_id', 'changed', 'created_at']) && id(v.operation_id) && (v.operation_type === 'save' || v.operation_type === 'restore') && id(v.result_revision_id) && typeof v.changed === 'boolean' && timestamp(v.created_at);
const operationResult = (v: unknown) => record(v) && exact(v, ['operation', 'current_revision_id']) && receipt(v.operation) && id(v.current_revision_id);
const operationStatus = (v: unknown) => operationResult(v) || (record(v) && exact(v, ['operation']) && v.operation === null);
const revisionKeys = ['revision_id', 'parent_revision_id', 'actor_id', 'actor_display_name', 'source_kind', 'restored_from_revision_id', 'created_at'];
const revisionFields = (v: RecordValue) => id(v.revision_id) && nullableId(v.parent_revision_id) && nullableId(v.actor_id) && (v.actor_display_name === null || string(v.actor_display_name)) && ['save', 'restore', 'import', 'unknown'].includes(String(v.source_kind)) && nullableId(v.restored_from_revision_id) && timestamp(v.created_at);
const revisionSummary = (v: unknown) => record(v) && exact(v, revisionKeys) && revisionFields(v);
const revisionView = (v: unknown) => record(v) && exact(v, [...revisionKeys, 'content']) && revisionFields(v) && string(v.content);
const revisionPage = (v: unknown) => record(v) && exact(v, ['revisions', 'head_revision_id', 'next_cursor']) && Array.isArray(v.revisions) && v.revisions.every(revisionSummary) && id(v.head_revision_id) && nullableId(v.next_cursor);
const revisionDiff = (v: unknown) => record(v) && exact(v, ['from_revision_id', 'to_revision_id', 'diff']) && id(v.from_revision_id) && id(v.to_revision_id) && string(v.diff);
const deletedPage = (v: unknown) => record(v) && exact(v, ['documents', 'next_cursor']) && Array.isArray(v.documents) && v.documents.every(d => record(d) && exact(d, ['object_id', 'name', 'path']) && id(d.object_id) && string(d.name) && string(d.path)) && nullableId(v.next_cursor);
const pageQuery = (cursor?: string, limit?: number) => (cursor === undefined ? '' : '&cursor=' + encodeURIComponent(cursor)) + (limit === undefined ? '' : '&limit=' + encodeURIComponent(limit));
const node = (v: unknown) => record(v) && string(v.id) && string(v.name) && string(v.kind) && (v.parent_id === null || string(v.parent_id)) && typeof v.version === 'number' && typeof v.position === 'number' && string(v.path) && string(v.created_at) && string(v.modified_at) && 'metadata' in v;
const access = (v: unknown) => record(v) && typeof v.version === 'number' && Array.isArray(v.actions) && v.actions.every(string) && string(v.visibility) && typeof v.frozen === 'boolean' && typeof v.can_freeze === 'boolean' && typeof v.can_unfreeze === 'boolean';
const user = (v: unknown) => record(v) && string(v.id) && string(v.login_name) && string(v.display_name) && string(v.status) && typeof v.site_admin === 'boolean' && typeof v.version === 'number';
const userResponse = (v: unknown) => record(v) && user(v.user);
const grant = (v: unknown) => userResponse(v) && record(v) && string(v.token) && string(v.purpose) && string(v.expires_at);
const workspace = (v: unknown) => record(v) && record(v.workspace) && typeof v.workspace.version === 'number' && string(v.workspace.read_scope) && Array.isArray(v.workspace.members) && v.workspace.members.every(m => record(m) && user(m.user) && string(m.role) && string(m.status));
const nodeResponse = (v: unknown) => record(v) && node(v.node) && (v.access === undefined || access(v.access));
const ok = (v: unknown) => record(v) && v.ok === true;
const session = (v: unknown) => record(v) && record(v.user) && string(v.user.id) && string(v.user.login_name) && string(v.user.display_name) && string(v.user.status) && typeof v.user.site_admin === 'boolean' && typeof v.user.version === 'number' && string(v.csrf) && string(v.expires_at);
const bootstrap = (v: unknown) => record(v) && typeof v.initialized === 'boolean' && (string(v.nonce) || (session(v) && record(v.scope) && id(v.scope.workspace_id) && id(v.scope.branch_id) && typeof v.workspace_access_version === 'number' && (v.workspace_role === null || string(v.workspace_role)) && (v.root === null || node(v.root)) && (v.root_access === null || access(v.root_access))));
const document = (v: unknown) => record(v) && node(v.document) && record(v.document) && string(v.document.content) && string(v.document.revision_id) && access(v.access);
export class ApiClient {
  epoch = 0; nonce: string | null = null; csrf: string | null = null;
  private transport: typeof fetch;
  // Native browser fetch rejects the ApiClient receiver when stored directly as a method.
  constructor(transport: typeof fetch = (...args) => fetch(...args)) { this.transport = transport; }
  invalidate() { this.epoch++; this.nonce = null; this.csrf = null; }
  private async request<T>(path: string, validate: (v: unknown) => boolean, method = 'GET', body?: unknown, proof?: 'nonce' | 'csrf'): Promise<T> {
    const epoch = this.epoch;
    const headers: Record<string, string> = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (proof) { const value = this[proof]; if (!value) throw new ApiError('unauthenticated', 'Authentication proof unavailable.'); headers[proof === 'nonce' ? 'X-C156-Nonce' : 'X-C156-CSRF'] = value; }
    let response: Response;
    try { const transport = this.transport; response = await transport(path, { method, credentials: 'same-origin', headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); }
    catch { if (epoch !== this.epoch) throw new ApiError('stale', 'Obsolete request.'); throw new ApiError('network', 'Request could not be completed.'); }
    if (epoch !== this.epoch) throw new ApiError('stale', 'Obsolete request.');
    let value: unknown;
    try { value = await response.json(); } catch { if (epoch !== this.epoch) throw new ApiError('stale', 'Obsolete request.'); throw new ApiError('response', 'Invalid server response.', response.status); }
    if (epoch !== this.epoch) throw new ApiError('stale', 'Obsolete request.');
    if (!response.ok) {
      if (response.status === 401) this.invalidate();
      if (record(value) && record(value.error) && string(value.error.code) && string(value.error.message)) throw new ApiError(value.error.code, value.error.message, response.status);
      throw new ApiError('response', 'Invalid server response.', response.status);
    }
    if (!validate(value)) throw new ApiError('response', 'Invalid server response.', response.status);
    return value as T;
  }
  async bootstrap() { const value = await this.request<Bootstrap>('/api/bootstrap', bootstrap); if ('nonce' in value) { this.nonce = value.nonce; this.csrf = null; } else { this.csrf = value.csrf; this.nonce = null; } return value; }
  async login(loginName: string, password: string) { const value = await this.request<Session>('/api/auth/login', session, 'POST', { login_name: loginName, password }, 'nonce'); this.csrf = value.csrf; this.nonce = null; return value; }
  async session() { const value = await this.request<Session>('/api/session', session); this.csrf = value.csrf; return value; }
  async logout() { await this.request('/api/auth/logout', v => record(v) && v.ok === true, 'POST', {}, 'csrf'); this.invalidate(); }
  listChildren(folderId: string) { return this.request<ChildrenResponse>(`/api/children?folder_id=${encodeURIComponent(folderId)}`, v => record(v) && Array.isArray(v.nodes) && v.nodes.every(n => node(n) && record(n) && access(n.access))); }
  readDocument(objectId: string) { return this.request<DocumentResponse>(`/api/document?object_id=${encodeURIComponent(objectId)}`, document); }
  saveDocument(value: SaveDocument) { return this.request<DocumentResponse>('/api/document', document, 'PUT', { object_id: value.object_id, content: value.content, expected_revision_id: value.expected_revision_id }, 'csrf'); }
  saveDocumentOperation(value: SaveDocumentOperation) { return this.request<OperationResult>('/api/document', operationResult, 'PUT', { object_id: value.object_id, content: value.content, expected_revision_id: value.expected_revision_id, operation_id: value.operation_id }, 'csrf'); }
  restoreDocument(value: RestoreDocumentOperation) { return this.request<OperationResult>('/api/document/restore', operationResult, 'POST', { object_id: value.object_id, source_revision_id: value.source_revision_id, expected_revision_id: value.expected_revision_id, operation_id: value.operation_id }, 'csrf'); }
  getOperationStatus(objectId: string, operationId: string) { return this.request<OperationStatus>(`/api/document/operation?object_id=${encodeURIComponent(objectId)}&operation_id=${encodeURIComponent(operationId)}`, operationStatus); }
  listDocumentRevisions(objectId: string, cursor?: string, limit?: number) { return this.request<RevisionPage>(`/api/document/history?object_id=${encodeURIComponent(objectId)}${pageQuery(cursor, limit)}`, revisionPage); }
  readDocumentRevision(objectId: string, revisionId: string) { return this.request<RevisionView>(`/api/document/revision?object_id=${encodeURIComponent(objectId)}&revision_id=${encodeURIComponent(revisionId)}`, revisionView); }
  compareDocumentRevisions(objectId: string, fromId: string, toId: string) { return this.request<RevisionDiff>(`/api/document/diff?object_id=${encodeURIComponent(objectId)}&from_revision_id=${encodeURIComponent(fromId)}&to_revision_id=${encodeURIComponent(toId)}`, revisionDiff); }
  listDeletedDocuments(cursor?: string, limit?: number) { const query = pageQuery(cursor, limit); return this.request<DeletedDocumentPage>('/api/documents/deleted' + (query ? '?' + query.slice(1) : ''), deletedPage); }
  activate(token: string, password: string) { return this.request<UserResponse>('/api/auth/activate', userResponse, 'POST', {token, password}, 'nonce'); }
  resetPassword(token: string, password: string) { return this.request<UserResponse>('/api/auth/reset', userResponse, 'POST', {token, password}, 'nonce'); }
  changeProfile(display_name: string, expected_version: number) { return this.request<UserResponse>('/api/account/profile', userResponse, 'PUT', {display_name, expected_version}, 'csrf'); }
  async changePassword(old_password: string, new_password: string) { await this.request('/api/account/password', ok, 'PUT', {old_password, new_password}, 'csrf'); this.invalidate(); }
  listUsers() { return this.request<{users: import('./types.ts').User[]}>('/api/admin/users', v => record(v) && Array.isArray(v.users) && v.users.every(user)); }
  createUser(login_name: string, display_name: string) { return this.request<AccountGrant>('/api/admin/users', grant, 'POST', {login_name, display_name}, 'csrf'); }
  userAction(action: UserAction, user_id: string, expected_version: number) { return this.request<UserResponse | AccountGrant>('/api/admin/users/' + action, action === 'activation' || action === 'reset' ? grant : userResponse, 'POST', {user_id, expected_version}, 'csrf'); }
  setSiteAdmin(user_id: string, enabled: boolean, expected_version: number) { return this.request<UserResponse>('/api/admin/users/site-admin', userResponse, 'PUT', {user_id, enabled, expected_version}, 'csrf'); }
  readMembers() { return this.request<WorkspaceResponse>('/api/workspace/members', workspace); }
  addMember(login_name: string, role: string, expected_version: number) { return this.request<WorkspaceResponse>('/api/workspace/members', workspace, 'POST', {login_name, role, expected_version}, 'csrf'); }
  setMemberRole(user_id: string, role: string, expected_version: number) { return this.request<WorkspaceResponse>('/api/workspace/members', workspace, 'PUT', {user_id, role, expected_version}, 'csrf'); }
  removeMember(user_id: string, expected_version: number) { return this.request<WorkspaceResponse>('/api/workspace/members', workspace, 'DELETE', {user_id, expected_version}, 'csrf'); }
  inviteMember(login_name: string, display_name: string, role: string, expected_version: number) { return this.request<import('./types.ts').WorkspaceInvitationResponse>('/api/workspace/invitations', value => grant(value) && workspace(value), 'POST', {login_name, display_name, role, expected_version}, 'csrf'); }
  setReadScope(read_scope: string, expected_version: number) { return this.request<WorkspaceResponse>('/api/workspace/read-scope', workspace, 'PUT', {read_scope, expected_version}, 'csrf'); }
  transferOwnership(target_user_id: string, expected_version: number) { return this.request<WorkspaceResponse>('/api/workspace/ownership', workspace, 'POST', {target_user_id, expected_version}, 'csrf'); }
  createFolder(parent_id: string, name: string) { return this.request<NodeResponse>('/api/folder', nodeResponse, 'POST', {parent_id, name}, 'csrf'); }
  createDocument(parent_id: string, name: string, visibility: 'inherit' | 'private' = 'inherit') { return this.request<DocumentResponse>('/api/document', document, 'POST', {parent_id, name, visibility}, 'csrf'); }
  renameNode(object_id: string, name: string, expected_version: number) { return this.request<NodeResponse>('/api/node/rename', nodeResponse, 'PUT', {object_id, name, expected_version}, 'csrf'); }
  prepareDelete(folderId: string) { return this.request<DeletePlan>('/api/folder/delete-plan?folder_id=' + encodeURIComponent(folderId), v => record(v) && string(v.object_id) && typeof v.version === 'number' && string(v.subtree_token) && Array.isArray(v.items) && v.items.every(i => record(i) && node(i.node) && typeof i.depth === 'number')); }
  deleteNode(object_id: string, expected_version: number, plan?: DeletePlan) { return this.request('/api/node', ok, 'DELETE', {object_id, expected_version, recursive: !!plan, ...(plan ? {expected_subtree_token: plan.subtree_token} : {})}, 'csrf'); }

}
