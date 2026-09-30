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
  forbidden: '请求无法通过本地访问校验，请刷新页面后重试。',
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
  }

  async request(path, { method = 'GET', body, signal } = {}) {
    const options = { method, signal };
    if (body !== undefined) {
      options.headers = {
        'Content-Type': 'application/json',
        'X-C156-Nonce': this.nonce,
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
    if (!response.ok) {
      throw new ApiError(data.error?.code, data.error?.message, response.status);
    }
    return data;
  }

  async bootstrap() {
    const data = await this.request('/api/bootstrap');
    this.nonce = data.nonce;
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

  createFolder(parentId, name) {
    return this.request('/api/folder', {
      method: 'POST',
      body: { parent_id: parentId, name },
    });
  }

  createDocument(parentId, name) {
    return this.request('/api/document', {
      method: 'POST',
      body: { parent_id: parentId, name },
    });
  }

  saveDocument(saveRequest) {
    return this.request('/api/document', { method: 'PUT', body: saveRequest });
  }
}
