// 在这里增加 API 方法。这个模块只负责 HTTP/JSON，不操作页面和编辑状态。
const messages = {
  conflict: '文档已被其他操作修改，请查看最新正文后手动合并。',
  already_exists: '这个目录内已有同名项目，请修改名称。',
  not_found: '项目不存在或已被删除。',
  invalid_name: '名称无效，请检查后重试。',
  invalid_operation: '此操作无法完成，请刷新目录后重试。',
  not_directory: '目标已不是目录，请刷新目录后重试。',
  not_document: '目标不是文档，请重新选择。',
  invalid_argument: '请求参数无效，请刷新页面后重试。',
  path_outside_root: '目标已不在当前工作目录内。',
  forbidden: '当前账号没有执行此操作的权限。',
  unauthenticated: '会话已失效。草稿已保留，请重新登录。',
  frozen: '文档已被他人冻结。草稿已保留，请先解除冻结。',
  rate_limited: '请求过于频繁，请稍后重试。',
  storage_busy: '服务暂时忙，请稍后重试。',
};

function errorMessage(code, message, status) {
  if (messages[code]) return messages[code];
  if (status === 413) return '正文过大，无法保存。';
  if (status >= 500) return '服务暂时无法完成操作，请稍后重试。';
  if (/[\u3400-\u9fff]/.test(message || '')) return message;
  return '操作失败，请刷新页面后重试。';
}

export class ApiError extends Error {
  constructor(code, message, status) {
    super(errorMessage(code, message, status));
    this.code = code;
    this.status = status;
  }
}

export class Client {
  constructor() {
    this.nonce = '';
    this.csrf = '';
    this.epoch = 0;
  }

  async request(path, { method = 'GET', body, signal } = {}) {
    const epoch = this.epoch;
    const options = { method, signal, credentials: 'same-origin' };
    if (body !== undefined) {
      options.headers = {
        'Content-Type': 'application/json',
        [path.startsWith('/api/auth/') && path !== '/api/auth/logout' ? 'X-C156-Nonce' : 'X-C156-CSRF']: path.startsWith('/api/auth/') && path !== '/api/auth/logout' ? this.nonce : this.csrf,
      };
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(path, options);
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new ApiError('network', '无法连接本地服务，请检查服务是否正在运行。');
    }

    let data;
    try {
      data = await response.json();
    } catch {
      throw new ApiError('response', '服务返回了无法读取的结果。');
    }
    if (epoch !== this.epoch) throw new ApiError('stale', '身份已变化，已忽略旧请求。', 0);
    if (!response.ok) {
      throw new ApiError(data.error?.code, data.error?.message, response.status);
    }
    return data;
  }

  async bootstrap() {
    const data = await this.request('/api/bootstrap');
    if (data.nonce) this.nonce = data.nonce;
    if (data.csrf) this.csrf = data.csrf;
    return data;
  }

  listChildren(folderId) {
    const query = new URLSearchParams({ folder_id: folderId });
    return this.request('/api/children?' + query);
  }

  readDocument(objectId, { signal } = {}) {
    const query = new URLSearchParams({ object_id: objectId });
    return this.request('/api/document?' + query, { signal });
  }

  createFolder(parentId, name, visibility = 'inherit') {
    return this.request('/api/folder', {
      method: 'POST',
      body: { parent_id: parentId, name, visibility },
    });
  }

  createDocument(parentId, name, visibility = 'inherit') {
    return this.request('/api/document', {
      method: 'POST',
      body: { parent_id: parentId, name, visibility },
    });
  }

  saveDocument(saveRequest) {
    return this.request('/api/document', { method: 'PUT', body: { object_id: saveRequest.object_id, content: saveRequest.content, expected_revision_id: saveRequest.expected_revision_id } });
  }
  invalidate() { this.epoch += 1; }
  login(login_name, password) { return this.request('/api/auth/login', {method:'POST',body:{login_name,password}}); }
  activate(token, password) { return this.request('/api/auth/activate', {method:'POST',body:{token,password}}); }
  reset(token, password) { return this.request('/api/auth/reset', {method:'POST',body:{token,password}}); }
  session() { return this.request('/api/session'); }
  logout() { return this.request('/api/auth/logout', {method:'POST',body:{}}); }
  changePassword(old_password,new_password) { return this.request('/api/account/password',{method:'PUT',body:{old_password,new_password}}); }
  profile(display_name,expected_version) { return this.request('/api/account/profile',{method:'PUT',body:{display_name,expected_version}}); }
  users() { return this.request('/api/admin/users'); }
  createUser(login_name,display_name) { return this.request('/api/admin/users',{method:'POST',body:{login_name,display_name}}); }
  userAction(action,user_id,expected_version) { return this.request('/api/admin/users/'+action,{method:'POST',body:{user_id,expected_version}}); }
  siteAdmin(user_id,enabled,expected_version) { return this.request('/api/admin/users/site-admin',{method:'PUT',body:{user_id,enabled,expected_version}}); }
  members() { return this.request('/api/workspace/members'); }
  addMember(login_name,role,expected_version) { return this.request('/api/workspace/members',{method:'POST',body:{login_name,role,expected_version}}); }
  changeMember(user_id,role,expected_version) { return this.request('/api/workspace/members',{method:'PUT',body:{user_id,role,expected_version}}); }
  removeMember(user_id,expected_version) { return this.request('/api/workspace/members',{method:'DELETE',body:{user_id,expected_version}}); }
  ownership(target_user_id,expected_version) { return this.request('/api/workspace/ownership',{method:'POST',body:{target_user_id,expected_version}}); }
  readScope(read_scope,expected_version) { return this.request('/api/workspace/read-scope',{method:'PUT',body:{read_scope,expected_version}}); }
  access(object_id) { return this.request('/api/access?'+new URLSearchParams({object_id})); }
  putRule(body) { return this.request('/api/access/rule',{method:'PUT',body}); }
  deleteRule({object_id,subject_type,subject_key,action,expected_version}) { return this.request('/api/access/rule',{method:'DELETE',body:{object_id,subject_type,subject_key,action,expected_version}}); }
  visibility(object_id,visibility,expected_version) { return this.request('/api/access/visibility',{method:'PUT',body:{object_id,visibility,expected_version}}); }
  freeze(object_id,expected_version,enabled) { return this.request('/api/document/freeze',{method:enabled?'POST':'DELETE',body:{object_id,expected_version}}); }

}
