import { Client } from './client.js';
import { DirectoryTree } from './directory.js';
import { EditorState } from './editor-state.js';
import { AccountPanel } from './account.js';
import { AccessPanel } from './access.js';
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
  onError: error=>{if(error.status===401) sessionLost();else showError(error);},
  onSelectFolder: ()=>renderEditorStatus(),
});
let loadController = null;
let previewTimer = null;
let confirmingSwitch = false;
let cancelSwitch = null;
let creating = false;
let merging = false;
let createKind = 'folder';
let user = null;
let role = null;
let documentAccess = null;
let pendingIdentity = null;
let bootSequence = 0;
let accessTarget = null;
const account = new AccountPanel({client,panel:element('account-panel'),run:runAction,getUser:()=>user,onLogin:login,onLogout:logout,onPassword:changePassword,onProfile:updated=>{user=updated;account.render(user);}});
const accessPanel = new AccessPanel({client,panel:element('access-panel'),run:runAction,changed:refreshWorkspace,getRole:()=>role,getObject:()=>accessTarget});
function hideDraft() {
  clearTimeout(previewTimer); ui.editor.value=''; ui.preview.replaceChildren();
  ui.latest.textContent=''; ui.oldDraft.textContent=''; ui.latestPanel.hidden=true;
  ui.comparison.hidden=true; ui.conflict.hidden=true;
}
function pauseIdentity() {
  client.invalidate(); state.setIdentity(null); creating=false;merging=false;ui.createButton.disabled=false;element('merge').disabled=false; user=null; role=null; documentAccess=null;
  loadController?.abort(); ui.createDialog.close();cancelSwitch?.();
  element('manage-access').hidden=true;element('manage-folder-access').hidden=true;element('access-panel').hidden=true;
  directory.clear(); account.clear(); accessPanel.clear(); hideDraft(); renderEditorStatus();
}
async function sessionLost() {
  pauseIdentity(); client.csrf=''; pendingIdentity=null;
  element('identity-gate').hidden=true;
  await bootstrapIdentity();
}
async function runAction(action, onConflict) {
  const epoch=client.epoch;
  try { return await action(); }
  catch(error) {
    if(epoch!==client.epoch || error.code==='stale') return;
    if(error.status===401) { await sessionLost(); showError(error); return; }
    showError(error);
    if(error.status===409 && onConflict) { await onConflict(); if(epoch===client.epoch) showError(new Error('配置已变化，已刷新。请检查后重新选择提交。')); }
  }
}
async function acceptIdentity(data,discard=false) {
  client.invalidate(); account.clear(); accessPanel.clear();element('access-panel').hidden=true; documentAccess=null;
  const acceptedEpoch=client.epoch;
  if(!state.setIdentity(data.user.id,{discard})) {
    user=null; role=null; documentAccess=null; pendingIdentity=data; directory.clear();hideDraft();
    element('identity-gate').hidden=false; renderEditorStatus();return;
  }
  pendingIdentity=null; element('identity-gate').hidden=true;
  user=data.user;role=data.workspace_role; client.csrf=data.csrf; directory.clear();
  account.render(user); element('manage-access').hidden=!['owner','admin'].includes(role);element('manage-folder-access').hidden=!['owner','admin'].includes(role);
  if(data.root) {directory.setRoot({...data.root,access:data.root_access});directory.selectedFolderId=data.root.id;await runAction(()=>directory.loadChildren(data.root.id));if(acceptedEpoch!==client.epoch)return;directory.expanded.add(data.root.id);directory.render();}
  if(state.document) {
    renderDocument();
    await refreshDocumentAccess();
  } else {hideDraft();renderEditorStatus();}
}
async function bootstrapIdentity() {
  const sequence=++bootSequence,epoch=client.epoch;
  try {
    const data=await client.bootstrap(); if(sequence!==bootSequence || epoch!==client.epoch)return;
    if(data.user) {await runAction(()=>acceptIdentity(data));}
    else {account.render(null,data.initialized);element('manage-access').hidden=true;renderEditorStatus();}
  } catch(error) {
    if(epoch!==client.epoch || error.code==='stale')return;
    if(error.status===401) {pauseIdentity();client.csrf='';await bootstrapIdentity();}
    else {documentAccess=null;renderEditorStatus();showError(error);}
  }
}
async function login(v) {
  const data=await client.login(v.login_name,v.password);
  // Cookie is already changed. Immediately invalidate every old request and hide old body.
  pauseIdentity(); client.csrf=data.csrf;
  await bootstrapIdentity();
}
async function logout() {
  if(state.shouldWarnBeforeUnload && !window.confirm('当前有未保存或尚未确认的保存。退出并保留本页草稿？')) return;
  pauseIdentity();
  const epoch=client.epoch;
  try {await client.logout();client.csrf='';pendingIdentity=null;element('identity-gate').hidden=true;await bootstrapIdentity();}
  catch(error){if(epoch!==client.epoch || error.code==='stale')return;if(error.status===401)await sessionLost();else {showError(error);account.panel.append(document.createTextNode('退出请求未确认，请重试。'));const retry=document.createElement('button');retry.textContent='重试退出';retry.onclick=()=>runAction(logout);account.panel.append(retry);}}
}
async function changePassword(v) {await client.changePassword(v.old_password,v.new_password);await sessionLost();}
async function refreshDocumentAccess() {
  if(!user || !state.document)return;
  const epoch=client.epoch;
  try {const result=await client.readDocument(state.document.id);if(epoch!==client.epoch)return;documentAccess=result.access;directory.remember(result.document);renderEditorStatus();}
  catch(error) {if(epoch!==client.epoch)return;documentAccess=null;renderEditorStatus();if(error.status===401)await sessionLost();else showError(error);}
}
async function freezeDocument(enabled) {
  if(!user || !documentAccess || !state.document)return;
  await runAction(async()=>{const {access}=await client.freeze(state.document.id,documentAccess.version,enabled);documentAccess=access;renderEditorStatus();},refreshDocumentAccess);
}


// 展示：状态更新只改按钮/标签；只有打开文档或手动合并才替换编辑框。
function showError(error, targetId = 'error') {
  const target = element(targetId);
  if(error?.code==='stale') return;
  target.textContent = error?.message || String(error);
  target.hidden = false;
}

function clearError(targetId = 'error') {
  const target = element(targetId);
  target.textContent = '';
  target.hidden = true;
}

function saveStatus() {
  if (state.paused) return '会话暂停 · 草稿已保留';
  if (state.uncertainSave) return '上次保存结果未确认 · 请重新保存或查看最新正文';
  if (state.saving) return '保存中…';
  if (state.conflict) return '保存冲突';
  if (state.dirty) return '未保存';
  if (state.comparisonDraft !== null) return '待合并';
  return state.document ? '已保存' : '就绪';
}

function renderEditorStatus() {
  const hasDocument = !!state.document && !!user && state.owner === user.id;
  const editable = hasDocument && documentAccess?.actions.includes('edit');
  ui.editor.disabled = !editable;
  ui.saveButton.disabled =
    !editable || (!state.dirty && !state.uncertainSave) || !!state.saving || state.conflict;
  ui.status.textContent = saveStatus();
  ui.path.textContent = hasDocument
    ? directory.nodes.get(state.document.id)?.path || state.document.path
    : '选择文档开始编辑';
  ui.conflict.hidden = !hasDocument || !state.conflict;
  element('access-status').textContent = hasDocument && documentAccess ? `授权 v${documentAccess.version} · ${documentAccess.visibility} · ${documentAccess.frozen?'已冻结':'未冻结'} · 动作：${documentAccess.actions.join(', ')}` : '';
  element('freeze').hidden = !hasDocument || !documentAccess?.can_freeze;
  element('unfreeze').hidden = !hasDocument || !documentAccess?.can_unfreeze;
  element('discard-draft').hidden=!hasDocument || !state.hasUnsavedWork;
  const canCreate=!!user && directory.nodes.get(directory.selectedFolderId)?.access?.actions.includes('create');
  element('new-folder').disabled=!canCreate;element('new-document').disabled=!canCreate;
  ui.comparison.hidden = !hasDocument || state.comparisonDraft === null;
  if (hasDocument && state.comparisonDraft !== null) ui.oldDraft.textContent = state.comparisonDraft;
}

function updatePreview() {
  try {
    renderPreview(user && state.owner===user.id && state.document ? state.draft : '', ui.preview);
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
  if(!user || !documentAccess?.actions.includes('edit'))return false;
  const epoch=client.epoch;
  if (state.saving) {
    showError(new Error('正在保存，请等待完成后再操作。'));
    return false;
  }
  if (state.conflict) {
    showError(new Error('请先查看最新正文并开始手动合并。'));
    return false;
  }
  if (!state.dirty && !state.uncertainSave) {
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
    const { document: snapshot, access } = await client.saveDocument(request);
    if(epoch!==client.epoch)return false;
    documentAccess=access;
    if (!state.saveSucceeded(request, snapshot)) return false;
    directory.remember(snapshot);
    directory.render();
    renderEditorStatus();
    return !state.hasUnsavedWork;
  } catch (error) {
    if(epoch!==client.epoch)return false;
    if(error.status===401){await sessionLost();return false;}
    state.saveFailed(request,error.code);
    if([403,404].includes(error.status)){documentAccess=null;await refreshDocumentAccess();}
    if(epoch!==client.epoch)return false;
    renderEditorStatus();
    showError(error);
    return false;
  }
}

function askSwitchAction() {
  return new Promise((resolve) => {
    const dialog = ui.switchDialog;
    function finish(choice) {
      cancelSwitch=null;
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
    cancelSwitch=()=>finish('cancel');
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
  if (!user || objectId === state.document?.id || confirmingSwitch) return;
  const epoch=client.epoch;
  confirmingSwitch = true;
  let allowed;
  try {
    allowed = await confirmLeavingDocument();
  } finally {
    confirmingSwitch = false;
  }
  if (!allowed || epoch!==client.epoch || !user) return;
  const sequence = state.beginLoad();
  loadController?.abort();
  loadController = new AbortController();
  ui.status.textContent = '加载中…';
  const previousDocumentId = state.document?.id;
  const previousDraft = state.draft;
  try {
    const { document: snapshot, access } = await client.readDocument(objectId, {
      signal: loadController.signal,
    });
    if (epoch!==client.epoch || sequence.sequence !== state.loading) return;
    // 等待服务器时仍可输入；新输入优先，不能被加载结果丢掉。
    if (state.document?.id !== previousDocumentId || state.draft !== previousDraft) {
      showError(new Error('加载期间正文发生修改，已保留当前草稿，请再次打开目标文档。'));
      return;
    }
    if (state.finishLoad(sequence, snapshot)) {
      documentAccess=access;
      clearError();
      renderDocument();
    }
  } catch (error) {
    if (epoch===client.epoch && sequence.sequence === state.loading && error.name !== 'AbortError') {if(error.status===401)await sessionLost();else showError(error);}
  } finally {
    if (epoch===client.epoch && sequence.sequence === state.loading) renderEditorStatus();
  }
}

async function refreshWorkspace() {
  if(!user) {await bootstrapIdentity();return;}
  await runAction(async()=>{const epoch=client.epoch;const data=await client.bootstrap();
    if(data.user.id!==user.id){await acceptIdentity(data);return;}
    role=data.workspace_role;user=data.user;account.render(user);
    element('manage-access').hidden=!['owner','admin'].includes(role);element('manage-folder-access').hidden=!['owner','admin'].includes(role);
    if(!['owner','admin'].includes(role)){accessPanel.clear();element('access-panel').hidden=true;}
    if(data.root){directory.setRoot({...data.root,access:data.root_access});await directory.refresh();}
    else directory.clear();
    if(epoch!==client.epoch)return;await refreshDocumentAccess();if(epoch!==client.epoch)return;directory.render();renderEditorStatus();});
}

// 新建：目录负责位置，Client 负责请求；失败时保留名称输入。
function showCreateDialog(kind) {
  const folderId = directory.selectedFolderId;
  if (!user || !folderId || !directory.nodes.get(folderId)?.access?.actions.includes('create')) return;
  const privateAllowed=['editor','admin','owner'].includes(role);
  element('create-visibility').value='inherit';
  element('create-visibility').options[1].disabled=!privateAllowed;
  element('create-hint').textContent=privateAllowed?'私密对象仅私密所有者和工作区管理者可通过。':'当前角色只能选择继承。';
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
  const epoch=client.epoch;
  creating = true;
  ui.createButton.disabled = true;
  clearError('create-error');
  const parentId = directory.selectedFolderId;
  const name = ui.createName.value;
  try {
    const result =
      createKind === 'folder'
        ? await client.createFolder(parentId, name, element('create-visibility').value)
        : await client.createDocument(parentId, name, element('create-visibility').value);
    if(epoch!==client.epoch)return;
    ui.createDialog.close();
    try {
      await directory.loadChildren(parentId);
      if(epoch!==client.epoch)return;
      directory.expanded.add(parentId);
      directory.render();
    } catch (error) {
      if(epoch!==client.epoch)return;
      if(error.status===401){await sessionLost();return;}
      showError(error);
    }
    if(epoch!==client.epoch)return;
    if (result.document) await openDocument(result.document.id);
  } catch (error) {
    if(epoch===client.epoch){if(error.status===401)await sessionLost();else showError(error, 'create-error');}
  } finally {
    if(epoch===client.epoch){creating = false;ui.createButton.disabled = false;}
  }
}

// 冲突：读取最新正文不改草稿；只有显式开始合并才换编辑基础。
async function fetchLatestDocument() {
  const epoch=client.epoch;
  if(!user)return null;
  const objectId = state.document?.id;
  if (!objectId) return null;
  const { document: snapshot } = await client.readDocument(objectId);
  if (epoch!==client.epoch || state.document?.id !== objectId) return null;
  if(!state.setLatest(snapshot,state.epoch))return null;
  ui.latest.textContent = snapshot.content;
  ui.latestPanel.hidden = false;
  return snapshot;
}

async function viewLatestDocument() {
  const epoch=client.epoch;
  try {
    await fetchLatestDocument();
    if(epoch===client.epoch)clearError('conflict-error');
  } catch (error) {
    if(epoch===client.epoch){if(error.status===401)await sessionLost();else showError(error, 'conflict-error');}
  }
}

async function startManualMerge() {
  if (merging) return;
  const epoch=client.epoch;
  merging = true;
  element('merge').disabled = true;
  try {
    if (await fetchLatestDocument() && epoch===client.epoch) {
      state.startMerge();
      clearError();
      renderDocument();
      ui.editor.focus();
    }
  } catch (error) {
    if(epoch===client.epoch){if(error.status===401)await sessionLost();else showError(error, 'conflict-error');}
  } finally {
    if(epoch===client.epoch){merging = false;element('merge').disabled = false;}
  }
}

async function copyText(value) {
  const epoch=client.epoch;
  if(!user || state.owner!==user.id)return;
  try {
    await navigator.clipboard.writeText(value);
    if(epoch===client.epoch)ui.status.textContent = '草稿已复制';
  } catch {
    if(epoch!==client.epoch)return;
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
  element('discard-draft').onclick=()=>{if(user && window.confirm('明确放弃当前草稿、合并对照与未确认保存？')){state.setIdentity(user.id,{discard:true});documentAccess=null;hideDraft();renderEditorStatus();}};
  element('freeze').onclick=()=>freezeDocument(true);element('unfreeze').onclick=()=>freezeDocument(false);
  element('manage-access').onclick=()=>{accessTarget=state.document?.id;element('access-panel').hidden=false;accessPanel.load();};
  element('manage-folder-access').onclick=()=>{accessTarget=directory.selectedFolderId;element('access-panel').hidden=false;accessPanel.load();};
  element('discard-identity').onclick=()=>runAction(async()=>{if(pendingIdentity)await acceptIdentity(pendingIdentity,true);});
  element('logout-pending').onclick=()=>runAction(logout);
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
    if(!user || state.owner!==user.id || ui.editor.disabled)return;
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

async function start() { bindEvents(); await bootstrapIdentity(); }
start();
