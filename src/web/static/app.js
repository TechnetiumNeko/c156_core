import { Client } from './client.js';
import { DirectoryTree } from './directory.js';
import { EditorState } from './editor-state.js';
import { renderPreview } from './preview.js';

// 页面入口：具体功能是下面的具名函数；所有按钮接线集中在 bindEvents()。
const element = (id) => document.getElementById(id);
const ui = {
  editor: element('editor'),
  preview: element('preview'),
  path: element('path'),
  status: element('status'),
  saveButton: element('save'),
  conflict: element('conflict'),
  comparison: element('comparison'),
  oldDraft: element('old-draft'),
  latest: element('latest'),
  latestPanel: element('latest-panel'),
  createDialog: element('create-dialog'),
  createTitle: element('create-title'),
  createLocation: element('create-location'),
  createName: element('name'),
  createButton: element('create-submit'),
  switchDialog: element('switch-dialog'),
};

const client = new Client();
const state = new EditorState();
const directory = new DirectoryTree({
  client,
  treeElement: element('tree'),
  folderLabel: element('active-folder'),
  onOpenDocument: openDocument,
  onError: showError,
});
let loadController = null;
let previewTimer = null;
let confirmingSwitch = false;
let creating = false;
let merging = false;
let createKind = 'folder';

// 展示：状态更新只改按钮/标签；只有打开文档或手动合并才替换编辑框。
function showError(error, targetId = 'error') {
  const target = element(targetId);
  target.textContent = error?.message || String(error);
  target.hidden = false;
}

function clearError(targetId = 'error') {
  const target = element(targetId);
  target.textContent = '';
  target.hidden = true;
}

function saveStatus() {
  if (state.saving) return '保存中…';
  if (state.conflict) return '保存冲突';
  if (state.dirty) return '未保存';
  if (state.comparisonDraft !== null) return '待合并';
  return state.document ? '已保存' : '就绪';
}

function renderEditorStatus() {
  const hasDocument = !!state.document;
  ui.editor.disabled = !hasDocument;
  ui.saveButton.disabled =
    !hasDocument || !state.dirty || !!state.saving || state.conflict;
  ui.status.textContent = saveStatus();
  ui.path.textContent = hasDocument
    ? directory.nodes.get(state.document.id)?.path || state.document.path
    : '选择文档开始编辑';
  ui.conflict.hidden = !state.conflict;
  ui.comparison.hidden = state.comparisonDraft === null;
  if (state.comparisonDraft !== null) ui.oldDraft.textContent = state.comparisonDraft;
}

function updatePreview() {
  try {
    renderPreview(state.document ? state.draft : '', ui.preview);
  } catch {
    showError(new Error('预览暂时无法显示，正文仍可编辑和保存。'));
  }
}

function renderDocument() {
  ui.editor.value = state.draft;
  ui.latestPanel.hidden = true;
  clearError('conflict-error');
  directory.selectDocument(state.document);
  renderEditorStatus();
  updatePreview();
}

// 编辑流程：发请求 → 更新纯状态 → 更新展示。
async function saveDocument() {
  clearError();
  if (state.saving) {
    showError(new Error('正在保存，请等待完成后再操作。'));
    return false;
  }
  if (state.conflict) {
    showError(new Error('请先查看最新正文并开始手动合并。'));
    return false;
  }
  if (!state.dirty) {
    if (state.comparisonDraft !== null) {
      showError(
        new Error('旧草稿尚未合并。请先合并需要保留的文字，再保存；也可明确放弃修改。'),
      );
      return false;
    }
    return true;
  }

  const request = state.beginSave();
  if (!request) return false;
  renderEditorStatus();
  try {
    const { document: snapshot } = await client.saveDocument(request);
    if (!state.saveSucceeded(request, snapshot)) return false;
    directory.remember(snapshot);
    directory.render();
    renderEditorStatus();
    return !state.hasUnsavedWork;
  } catch (error) {
    state.saveFailed(request, error.code);
    renderEditorStatus();
    showError(error);
    return false;
  }
}

function askSwitchAction() {
  return new Promise((resolve) => {
    const dialog = ui.switchDialog;
    function finish(choice) {
      dialog.removeEventListener('click', onClick);
      dialog.removeEventListener('cancel', onCancel);
      dialog.close();
      resolve(choice);
    }
    function onClick(event) {
      const choice = event.target.dataset.choice;
      if (choice) finish(choice);
    }
    function onCancel(event) {
      event.preventDefault();
      finish('cancel');
    }
    dialog.addEventListener('click', onClick);
    dialog.addEventListener('cancel', onCancel);
    dialog.showModal();
  });
}

async function confirmLeavingDocument() {
  if (state.saving) {
    showError(new Error('正在保存，请等待完成后再切换文档。'));
    return false;
  }
  if (!state.hasUnsavedWork) return true;
  const choice = await askSwitchAction();
  if (choice === 'save') return saveDocument();
  return choice === 'discard';
}

async function openDocument(objectId) {
  if (objectId === state.document?.id || confirmingSwitch) return;
  confirmingSwitch = true;
  let allowed;
  try {
    allowed = await confirmLeavingDocument();
  } finally {
    confirmingSwitch = false;
  }
  if (!allowed) return;

  const sequence = state.beginLoad();
  loadController?.abort();
  loadController = new AbortController();
  ui.status.textContent = '加载中…';
  const previousDocumentId = state.document?.id;
  const previousDraft = state.draft;
  try {
    const { document: snapshot } = await client.readDocument(objectId, {
      signal: loadController.signal,
    });
    if (sequence !== state.loading) return;
    // 等待服务器时仍可输入；新输入优先，不能被加载结果丢掉。
    if (state.document?.id !== previousDocumentId || state.draft !== previousDraft) {
      showError(new Error('加载期间正文发生修改，已保留当前草稿，请再次打开目标文档。'));
      return;
    }
    if (state.finishLoad(sequence, snapshot)) {
      clearError();
      renderDocument();
    }
  } catch (error) {
    if (sequence === state.loading && error.name !== 'AbortError') showError(error);
  } finally {
    if (sequence === state.loading) renderEditorStatus();
  }
}

async function refreshWorkspace() {
  clearError();
  try {
    const { root } = await client.bootstrap();
    directory.setRoot(root);
    await directory.refresh();
    if (state.document) {
      // 只取最新路径供目录/标题展示，正文和保存基础仍留在 state 中。
      const { document: snapshot } = await client.readDocument(state.document.id);
      directory.remember(snapshot);
    }
  } catch (error) {
    showError(error);
  }
  directory.render();
  renderEditorStatus();
}

// 新建：目录负责位置，Client 负责请求；失败时保留名称输入。
function showCreateDialog(kind) {
  const folderId = directory.selectedFolderId;
  if (!folderId) return;
  createKind = kind;
  ui.createTitle.textContent = kind === 'folder' ? '新建目录' : '新建文档';
  ui.createLocation.textContent = '位置：' + (directory.nodes.get(folderId)?.path || '');
  ui.createName.value = '';
  clearError('create-error');
  ui.createDialog.showModal();
  ui.createName.focus();
}

async function createNode(event) {
  event.preventDefault();
  if (creating) return;
  creating = true;
  ui.createButton.disabled = true;
  clearError('create-error');
  const parentId = directory.selectedFolderId;
  const name = ui.createName.value;
  try {
    const result =
      createKind === 'folder'
        ? await client.createFolder(parentId, name)
        : await client.createDocument(parentId, name);
    ui.createDialog.close();
    try {
      await directory.loadChildren(parentId);
      directory.expanded.add(parentId);
      directory.render();
    } catch (error) {
      showError(error);
    }
    if (result.document) await openDocument(result.document.id);
  } catch (error) {
    showError(error, 'create-error');
  } finally {
    creating = false;
    ui.createButton.disabled = false;
  }
}

// 冲突：读取最新正文不改草稿；只有显式开始合并才换编辑基础。
async function fetchLatestDocument() {
  const objectId = state.document?.id;
  if (!objectId) return null;
  const { document: snapshot } = await client.readDocument(objectId);
  if (state.document?.id !== objectId) return null;
  state.setLatest(snapshot);
  ui.latest.textContent = snapshot.content;
  ui.latestPanel.hidden = false;
  return snapshot;
}

async function viewLatestDocument() {
  try {
    await fetchLatestDocument();
    clearError('conflict-error');
  } catch (error) {
    showError(error, 'conflict-error');
  }
}

async function startManualMerge() {
  if (merging) return;
  merging = true;
  element('merge').disabled = true;
  try {
    if (await fetchLatestDocument()) {
      state.startMerge();
      clearError();
      renderDocument();
      ui.editor.focus();
    }
  } catch (error) {
    showError(error, 'conflict-error');
  } finally {
    merging = false;
    element('merge').disabled = false;
  }
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    ui.status.textContent = '草稿已复制';
  } catch {
    showError(new Error('浏览器无法复制。请选中只读草稿或编辑框中的文字，手动复制。'));
  }
}

function setViewMode(mode) {
  element('panes').dataset.mode = mode;
  for (const button of document.querySelectorAll('button[data-mode]')) {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
}

// 新按钮从这里接入：按钮 ID → 具名操作函数，不需要注册器或框架。
function bindEvents() {
  element('new-folder').onclick = () => showCreateDialog('folder');
  element('new-document').onclick = () => showCreateDialog('document');
  element('refresh').onclick = refreshWorkspace;
  ui.saveButton.onclick = saveDocument;
  element('create-form').addEventListener('submit', createNode);
  element('create-cancel').onclick = () => {
    if (!creating) ui.createDialog.close();
  };
  ui.createDialog.addEventListener('cancel', (event) => {
    if (creating) event.preventDefault();
  });

  element('view-latest').onclick = viewLatestDocument;
  element('merge').onclick = startManualMerge;
  element('copy-draft').onclick = () => copyText(state.draft);
  element('copy-comparison').onclick = () => copyText(state.comparisonDraft);
  element('toggle-tree').onclick = () => {
    document.querySelector('.workspace').classList.toggle('tree-open');
  };
  for (const button of document.querySelectorAll('button[data-mode]')) {
    button.onclick = () => setViewMode(button.dataset.mode);
  }

  ui.editor.addEventListener('input', () => {
    state.edit(ui.editor.value);
    renderEditorStatus();
    clearTimeout(previewTimer);
    previewTimer = setTimeout(updatePreview, 150);
  });
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      saveDocument();
    }
  });
  window.addEventListener('beforeunload', (event) => {
    if (state.shouldWarnBeforeUnload) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
}

async function start() {
  bindEvents();
  try {
    const { root } = await client.bootstrap();
    directory.setRoot(root);
    await directory.loadChildren(root.id);
    directory.expanded.add(root.id);
    directory.selectFolder(root.id);
    renderEditorStatus();
    const firstDocumentId = directory.children
      .get(root.id)
      .find((id) => directory.nodes.get(id).kind === 'document');
    if (firstDocumentId) await openDocument(firstDocumentId);
  } catch (error) {
    showError(error);
  }
}

start();
