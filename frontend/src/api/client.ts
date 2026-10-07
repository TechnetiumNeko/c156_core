import type { Bootstrap, Session, ChildrenResponse, DocumentResponse, SaveDocument } from './types.ts';
export class ApiError extends Error {
  code: string; status: number;
  constructor(code: string, message: string, status = 0) { super(message); this.name = 'ApiError'; this.code = code; this.status = status; }
}
type RecordValue = Record<string, unknown>;
const record = (v: unknown): v is RecordValue => typeof v === 'object' && v !== null && !Array.isArray(v);
const string = (v: unknown): v is string => typeof v === 'string';
const node = (v: unknown) => record(v) && string(v.id) && string(v.name) && string(v.kind) && (v.parent_id === null || string(v.parent_id)) && typeof v.version === 'number' && typeof v.position === 'number' && string(v.path) && string(v.created_at) && string(v.modified_at) && 'metadata' in v;
const access = (v: unknown) => record(v) && typeof v.version === 'number' && Array.isArray(v.actions) && v.actions.every(string) && string(v.visibility) && typeof v.frozen === 'boolean' && typeof v.can_freeze === 'boolean' && typeof v.can_unfreeze === 'boolean';
const session = (v: unknown) => record(v) && record(v.user) && string(v.user.id) && string(v.user.login_name) && string(v.user.display_name) && string(v.user.status) && typeof v.user.site_admin === 'boolean' && typeof v.user.version === 'number' && string(v.csrf) && string(v.expires_at);
const bootstrap = (v: unknown) => record(v) && typeof v.initialized === 'boolean' && (string(v.nonce) || (session(v) && typeof v.workspace_access_version === 'number' && string(v.workspace_role) && (v.root === null || node(v.root)) && (v.root_access === null || access(v.root_access))));
const document = (v: unknown) => record(v) && node(v.document) && record(v.document) && string(v.document.content) && string(v.document.revision_id) && access(v.access);
export class ApiClient {
  epoch = 0; nonce: string | null = null; csrf: string | null = null;
  private transport: typeof fetch;
  constructor(transport: typeof fetch = fetch) { this.transport = transport; }
  invalidate() { this.epoch++; this.nonce = null; this.csrf = null; }
  private async request<T>(path: string, validate: (v: unknown) => boolean, method = 'GET', body?: unknown, proof?: 'nonce' | 'csrf'): Promise<T> {
    const epoch = this.epoch;
    const headers: Record<string, string> = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (proof) { const value = this[proof]; if (!value) throw new ApiError('unauthenticated', 'Authentication proof unavailable.'); headers[proof === 'nonce' ? 'X-C156-Nonce' : 'X-C156-CSRF'] = value; }
    let response: Response;
    try { response = await this.transport(path, { method, credentials: 'same-origin', headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); }
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
}
