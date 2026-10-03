// 目录的缓存、展开状态和 DOM 都在这里；打开文档交给 app.js。
export class DirectoryTree {
  constructor({ client, treeElement, folderLabel, onOpenDocument, onError, onSelectFolder }) {
    this.client = client;
    this.treeElement = treeElement;
    this.folderLabel = folderLabel;
    this.onOpenDocument = onOpenDocument;
    this.onError = onError;
    this.onSelectFolder = onSelectFolder;

    this.root = null;
    this.nodes = new Map(); // 对象 ID → 服务返回的快照
    this.children = new Map(); // 目录 ID → 子对象 ID，保留服务的排列顺序
    this.expanded = new Set();
    this.selectedFolderId = null;
    this.selectedDocumentId = null;
  }

  clear() {
    this.root = null; this.nodes.clear(); this.children.clear(); this.expanded.clear();
    this.selectedFolderId = null; this.selectedDocumentId = null; this.render();
  }

  setRoot(root) {
    this.root = root;
    this.remember(root);
  }

  remember(node) {
    this.nodes.set(node.id, node);
  }

  async loadChildren(folderId) {
    const epoch = this.client.epoch;
    const { nodes } = await this.client.listChildren(folderId);
    if (epoch !== this.client.epoch) return;
    this.children.set(
      folderId,
      nodes.map((node) => node.id),
    );
    for (const node of nodes) this.remember(node);
  }

  selectFolder(folderId) {
    this.selectedFolderId = folderId;
    this.render();
    this.onSelectFolder?.();
  }

  selectDocument(snapshot) {
    this.remember(snapshot);
    this.selectedDocumentId = snapshot.id;
    if (snapshot.parent_id) this.selectedFolderId = snapshot.parent_id;
    this.render();
  }

  async toggleFolder(folderId) {
    const epoch = this.client.epoch;
    this.selectFolder(folderId);
    if (this.expanded.has(folderId)) {
      this.expanded.delete(folderId);
      this.render();
      return;
    }

    try {
      // 首次展开才发请求；刷新时重新读取已经展开的目录。
      if (!this.children.has(folderId)) await this.loadChildren(folderId);
      if (epoch !== this.client.epoch) return;
      this.expanded.add(folderId);
      this.render();
    } catch (error) {
      if (epoch === this.client.epoch) this.onError(error);
    }
  }

  async refresh() {
    if (!this.root) return;
    const epoch = this.client.epoch;
    const folderIds = new Set([this.root.id, ...this.expanded, this.selectedFolderId]);
    for (const folderId of folderIds) {
      if (!folderId) continue;
      try {
        await this.loadChildren(folderId);
        if (epoch !== this.client.epoch) return;
      } catch (error) {
        if (epoch !== this.client.epoch) return;
        if (error.status === 404) {
          this.expanded.delete(folderId);
          this.children.delete(folderId);
          if (folderId === this.selectedFolderId) {
            this.selectedFolderId = this.root.id;
          }
        }
        this.onError(error);
        if (epoch !== this.client.epoch) return;
      }
    }
    this.render();
  }

  render() {
    this.treeElement.replaceChildren();
    if (!this.root) { this.folderLabel.textContent = ''; return; }

    const folder = this.nodes.get(this.selectedFolderId);
    this.folderLabel.textContent = '新建位置：' + (folder?.path || folder?.name || '');
    const list = document.createElement('ul');
    list.append(this.renderNode(this.nodes.get(this.root.id)));
    this.treeElement.append(list);
  }

  renderNode(node) {
    const item = document.createElement('li');
    const row = document.createElement('div');
    row.className = 'tree-row';
    if (node.id === this.selectedDocumentId || node.id === this.selectedFolderId) {
      row.classList.add('selected');
    }

    const isFolder = node.kind === 'folder';
    const isExpanded = this.expanded.has(node.id);
    if (isFolder) {
      const toggle = document.createElement('button');
      toggle.className = 'expand';
      toggle.textContent = isExpanded ? '▾' : '▸';
      toggle.setAttribute('aria-label', (isExpanded ? '收起' : '展开') + node.name);
      toggle.setAttribute('aria-expanded', String(isExpanded));
      toggle.onclick = () => this.toggleFolder(node.id);
      row.append(toggle);
    }

    const label = document.createElement('button');
    label.className = 'node-label';
    label.textContent = (isFolder ? '▱ ' : '▤ ') + node.name;
    label.onclick = () => {
      if (isFolder) this.selectFolder(node.id);
      else this.onOpenDocument(node.id);
    };
    row.append(label);
    item.append(row);

    if (isFolder && isExpanded) {
      const list = document.createElement('ul');
      for (const childId of this.children.get(node.id) || []) {
        const child = this.nodes.get(childId);
        if (child) list.append(this.renderNode(child));
      }
      item.append(list);
    }
    return item;
  }
}
