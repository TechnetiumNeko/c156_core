<script setup lang="ts">
import { computed, h, markRaw, onMounted, onUnmounted, reactive, ref, watch, defineAsyncComponent } from 'vue';
import { NButton, NAlert, NDrawer, NDrawerContent, useMessage } from 'naive-ui';
import { useConfirm } from './composables/useConfirm.ts';
import { useFileOperations } from './composables/useFileOperations.ts';
import { errorText } from './composables/errors.ts';
import { themeVariables } from './theme.ts';
const AccountPage = defineAsyncComponent(() => import('./pages/AccountPage.vue'));
const AdminUsersPage = defineAsyncComponent(() => import('./pages/AdminUsersPage.vue'));
const MembersPage = defineAsyncComponent(() => import('./pages/MembersPage.vue'));
import FileActions from './components/FileActions.vue';
import { ApiClient, ApiError } from './api/client.ts';
import { DocumentSession } from './state/document-session.ts';
import DraftRecovery from './components/DraftRecovery.vue';
import DocumentHistory from './components/DocumentHistory.vue';
import DeletedDocuments from './components/DeletedDocuments.vue';
import { EditorState } from './state/editor.ts';
import { SessionState } from './state/session.ts';
import { DirectoryState } from './state/directory.ts';
import LoginPanel from './components/LoginPanel.vue';
import DirectoryTree from './components/DirectoryTree.vue';
import DocumentEditor from './components/DocumentEditor.vue';
const confirm = useConfirm();
const notices = useMessage();
function notify(text: string, type: 'success' | 'warning' | 'error') {
  notices.create(() => h('span', {role: type === 'success' ? 'status' : 'alert'}, text), {type, duration: type === 'success' ? 3200 : 6500});
}
const page = ref<'documents' | 'account' | 'admin' | 'members'>('documents');
const client = markRaw(new ApiClient());
const editor = reactive(new EditorState()) as EditorState;
const documents = reactive(new DocumentSession(client, editor)) as DocumentSession;
const session = reactive(new SessionState(client, editor, {beforeLeaveIdentity: () => documents.leave(), retainedIdentity: () => documents.retainedIdentity})) as SessionState;
const directory = reactive(new DirectoryState(client)) as DirectoryState;
const busy = ref(false); const loadingDocument = ref(false); const loadingLatest = ref(false);
const message = ref(''); const ready = ref(false);
const access = computed({get: () => documents.access, set: value => {documents.access = value;}});
const logoutFailed = ref(false); const showHistory = ref(false); const showDeleted = ref(false);
const localObjectId = ref('');
const accessRefreshFailed = ref(false); const loadingAccess = ref(false);
const files = useFileOperations(client, session, directory, editor, select, failure, confirm, documents);
watch(message, text => { if (text) notify(text, 'warning'); });
watch(files.message, text => {
  if (text) notify(text, files.messageType.value === 'error' ? 'error' : 'success');
});
const fileBusy = files.busy;
const navigationBusy = computed(() => busy.value || loadingAccess.value || loadingDocument.value || documents.busy || fileBusy.value);
const managesWorkspace = computed(() => session.workspaceRole === 'admin' || session.workspaceRole === 'owner');
watch(() => session.epoch, () => { notices.destroyAll(); files.reset(); showHistory.value = false; showDeleted.value = false; }, {flush: 'sync'});
watch(() => [session.user?.id, session.user?.site_admin, session.workspaceRole], () => {if (!session.user || (page.value === 'admin' && !session.user.site_admin) || (page.value === 'members' && !managesWorkspace.value)) page.value = 'documents';});
let accessRequest = 0;
const editable = computed(() => !!access.value?.actions.includes('edit') && !session.recoveryNeeded && documents.canEdit);
const canSave = computed(() => editable.value && !documents.pending && !documents.busy);
const status = computed(() => editor.paused ? '会话暂停或此页只读' : documents.busy ? '正在确认操作，可继续输入' : documents.pending ? '操作结果待确认' : editor.conflict ? '修订冲突，需比较' : documents.operationError ? '保存受阻' : !editable.value ? '只读' : editor.dirty ? '尚未保存到服务器' : editor.comparisonDraft !== null ? '合并参考仍保留' : '已存服务器');
function syncDirectory() { directory.setRoot(session.root, session.user?.id ?? null); }
async function failure(error: unknown, epoch: number) {
  if (epoch !== session.epoch || (error instanceof ApiError && error.code === 'stale')) return;
  message.value = errorText(error);
  if (error instanceof ApiError && error.status === 401 && session.user) {
    directory.reset();
    busy.value = true;
    try { await session.expire(); syncDirectory(); ready.value = true; message.value = session.user ? '' : '会话已失效，草稿已保留。请重新登录。'; }
    catch (recovery) { message.value = recovery instanceof Error ? recovery.message : '会话恢复失败，请重试'; ready.value = false; }
    finally { busy.value = false; }
  } else if (error instanceof ApiError && error.status === 401 && !client.nonce && !session.recoveryNeeded) {
    const text = '账号、密码或凭据不正确，请检查后重试。';
    try {await session.bootstrap(); syncDirectory(); ready.value = true;}
    catch {ready.value = false;}
    message.value = text;
  }
}
async function bootstrap() {
  busy.value = true; message.value = ''; ready.value = false;
  try { await session.bootstrap(); syncDirectory(); ready.value = true; await refreshDocumentAccess(); }
  catch (error) { await failure(error, session.epoch); }
  finally { busy.value = false; }
}
async function refreshDocumentAccess() {
  if (loadingAccess.value || !session.user || editor.paused || !editor.document) return;
  access.value = null; accessRefreshFailed.value = false; loadingAccess.value = true; message.value = '';
  const request = ++accessRequest;
  const epoch = session.epoch; const editorEpoch = editor.epoch; const document = editor.document;
  const current = () => request === accessRequest && epoch === session.epoch && editorEpoch === editor.epoch && editor.document === document && !editor.paused;
  try { const value = await client.readDocument(document.id); if (current()) access.value = value.access; }
  catch (error) {
    if (!current() || (error instanceof ApiError && error.code === 'stale')) return;
    accessRefreshFailed.value = true;
    await failure(error, epoch);
  }
  finally { if (request === accessRequest) loadingAccess.value = false; }
}
async function login(name: string, password: string) {
  busy.value = true; message.value = ''; directory.reset();
  try { await session.login(name, password); syncDirectory(); ready.value = true; await refreshDocumentAccess(); }
  catch (error) { await failure(error, session.epoch); }
  finally { busy.value = false; }
}
async function logout() {
  busy.value = true; message.value = ''; logoutFailed.value = false;
  try { await session.logout(); syncDirectory(); ready.value = true; }
  catch (error) { logoutFailed.value = documents.localStatus.kind === 'error'; await failure(error, session.epoch); syncDirectory(); }
  finally { busy.value = false; }
}
async function forceLogout() {
  if (!window.confirm('本机存储失败。请先复制当前文字；继续退出可能丢失尚未落盘的内容。此前已存的本机稿仍会保留。确定继续退出？')) return;
  try { await documents.leave({acknowledgeUnstoredLoss: true}); await logout(); }
  catch (error) { await failure(error, session.epoch); }
}
async function acceptPending() {
  busy.value = true;
  try { if (await session.acceptPending({discard: true})) { syncDirectory(); ready.value = true; message.value = ''; } }
  catch (error) { await failure(error, session.epoch); }
  finally { busy.value = false; }
}
async function toggle(id: string) {
  const epoch = session.epoch;
  try { await directory.toggle(id); } catch (error) { await failure(error, epoch); }
}
async function select(id: string) {
  if (loadingDocument.value || documents.busy || !session.scope || !session.user) return;
  const epoch = session.epoch; loadingDocument.value = true; message.value = ''; showHistory.value = false;
  try { await documents.open(session.scope, id, session.user.id); localObjectId.value = id; }
  catch (error) { await failure(error, epoch); }
  finally { loadingDocument.value = false; }
}
async function save() {
  if (!canSave.value || fileBusy.value) return;
  message.value = '';
  const epoch = session.epoch; const revision = editor.revision;
  try {
    await documents.save();
    if (epoch === session.epoch && editor.revision !== revision && !documents.pending && !documents.operationError) notify('已保存到服务器。', 'success');
  }
  catch (error) { await failure(error, session.epoch); }
}
function edit(text: string) {
  try { documents.edit(text); } catch (error) { void failure(error, session.epoch); }
}
async function latest() {
  if (!editor.document || editor.paused) return;
  const ticket = editor.beginLatest(); const epoch = session.epoch; loadingLatest.value = true; message.value = '';
  try { const value = await client.readDocument(editor.document.id); if (editor.setLatest(value.document, ticket.epoch, ticket)) access.value = value.access; }
  catch (error) { await failure(error, epoch); }
  finally { loadingLatest.value = false; }
}
async function merge() { if (documents.pending || documents.busy) return; if (window.confirm('以最新正文开始手动合并？当前草稿仅在此页面的只读参考区保留。离开前需要合并、复制或明确移除这份参考。')) { try { await documents.startMerge(); } catch (error) { await failure(error, session.epoch); } } }
async function passwordRevoked() {
  busy.value = true; access.value = null; directory.reset();
  try {await session.expire(); ready.value = true; message.value = '密码已修改，请重新登录。未保存的草稿仍保留。';}
  catch (error) {ready.value = false; message.value = '密码已修改，会话加载失败。请重新加载会话，草稿仍保留。';}
  finally {busy.value = false;}
}
function beforeUnload(event: BeforeUnloadEvent) { if (editor.shouldWarnBeforeUnload) { event.preventDefault(); event.returnValue = ''; } }
function keydown(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void save(); } }
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); window.addEventListener('keydown', keydown); void bootstrap(); });
onUnmounted(() => { window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('keydown', keydown); });
</script>
<template>
  <div class="app-shell">
    <header class="app-header">
      <button class="brand" @click="page = 'documents'" aria-label="返回文档工作台"><span class="brand-mark">C</span><span>C156<span class="brand-caption">共享文档</span></span></button>
      <nav v-if="session.user" class="toolbar-row" aria-label="工作台导航">
        <NButton :type="page === 'documents' ? 'primary' : 'default'" quaternary :disabled="navigationBusy" @click="page = 'documents'">文档</NButton>
        <NButton :type="page === 'account' ? 'primary' : 'default'" quaternary :disabled="navigationBusy" @click="page = 'account'">个人账号</NButton>
        <NButton v-if="session.user.site_admin" :type="page === 'admin' ? 'primary' : 'default'" quaternary :disabled="navigationBusy" @click="page = 'admin'">站点账号</NButton>
        <NButton v-if="managesWorkspace" :type="page === 'members' ? 'primary' : 'default'" quaternary :disabled="navigationBusy" @click="page = 'members'">工作区管理</NButton>
      </nav>
      <div v-if="session.user" class="toolbar-row ml-auto"><span class="text-sm text-muted max-w-36 truncate">{{session.user.display_name}}</span><NButton size="small" :disabled="navigationBusy" @click="logout">退出</NButton></div>
    </header>
    <div v-if="busy || (!ready || session.recoveryNeeded) || (ready && !session.initialized) || session.blocked" class="app-notices">
      <p v-if="busy" class="muted-copy m-0" role="status">正在处理…</p>
      <NButton v-if="(!ready || session.recoveryNeeded) && !busy" size="small" @click="bootstrap">重新加载会话（保留草稿）</NButton>
      <NAlert v-if="ready && !session.initialized" type="info">数据库尚未初始化，请联系管理员。</NAlert>
      <NAlert v-if="session.blocked" type="warning" role="alert">已登录另一个账号。原账号草稿仍保留，排空本机写入后可继续。<NButton class="mt-3" :disabled="busy" @click="acceptPending">保留已存草稿并继续</NButton></NAlert>
    </div>
    <main v-show="page === 'documents'" class="workbench" :class="{'anonymous-workbench': !session.user}">
      <aside v-if="session.user" class="directory-sidebar">
        <div class="toolbar-row justify-between mb-5"><h2 class="section-title">目录</h2><NButton v-if="session.root && session.rootAccess?.actions.includes('create')" size="small" :disabled="navigationBusy" @click="files.open('document', session.root)">新建文档</NButton></div>
        <DirectoryTree v-if="directory.root" :node="directory.root" :access="session.rootAccess" :state="directory" :selected="editor.document?.id ?? null" :disabled="navigationBusy" @toggle="toggle" @select="select" @action="files.open" @failure="failure($event, session.epoch)" />
        <p v-else class="muted-copy">没有可访问的根目录。请联系工作区管理员确认成员资格。</p>
        <NButton v-if="managesWorkspace" class="mt-3" :disabled="navigationBusy" @click="showDeleted = !showDeleted">已删除文档</NButton>
        <details class="mt-3"><summary>找回无法打开文档的本机稿</summary><p class="muted-copy">输入曾打开文档的标识，仅检查当前账号的这一篇本机稿。</p><input v-model="localObjectId" aria-label="文档标识" /><NButton :disabled="navigationBusy || !localObjectId.trim()" @click="select(localObjectId.trim())">检查本机稿</NButton></details>
        <p class="directory-help muted-copy">右键目录项或点击操作按钮，可新建、重命名和删除。</p>
      </aside>
      <aside v-else-if="!session.blocked" class="login-surface"><LoginPanel :client="client" :busy="busy" :ready="ready && !session.recoveryNeeded && session.initialized" @login="login" @failure="failure($event, session.epoch)" /></aside>
      <article class="editor-area" :class="{'anonymous-empty': !session.user && !editor.document}">
        <p v-if="loadingDocument" class="muted-copy" role="status">正在读取文档…</p>
        <NButton v-if="accessRefreshFailed && session.user && !editor.paused && editor.document" :disabled="navigationBusy" class="mb-3" @click="refreshDocumentAccess">重新读取文档权限（保留草稿）</NButton>
        <DraftRecovery :documents="documents" :editor="editor" :authenticated="!!session.user && !session.recoveryNeeded" :logout-failed="logoutFailed" @failure="failure($event, session.epoch)" @force-logout="forceLogout" />
        <DocumentEditor :editor="editor" :editable="editable" :can-save="canSave" :can-view-history="!!session.user && !!access?.actions.includes('history_read')" :status="status" :busy="busy || loadingLatest || loadingAccess || documents.busy || !!documents.pending" @edit="edit" @save="save" @history="showHistory = true" @latest="latest" @merge="merge" />
        <DeletedDocuments v-if="showDeleted && session.user && managesWorkspace" :key="session.epoch" :client="client" @failure="failure($event, session.epoch)" />
      </article>
    </main>
    <template v-if="session.user">
      <AccountPage v-if="page === 'account'" :key="session.epoch" :client="client" :user="session.user" :workspace-role="session.workspaceRole" @profile="session.user = $event" @revoked="passwordRevoked" @failure="failure($event, session.epoch)" />
      <AdminUsersPage v-if="page === 'admin' && session.user.site_admin" :key="session.epoch" :client="client" :user="session.user" @profile="session.user = $event" @failure="failure($event, session.epoch)" />
      <MembersPage v-if="page === 'members' && managesWorkspace" :key="session.user.id" :client="client" :user="session.user" :workspace-role="session.workspaceRole" @refresh="bootstrap" @failure="failure($event, session.epoch)" />
    </template>
    <NDrawer :show="showHistory && !!session.user && !!editor.document && !!access?.actions.includes('history_read')" width="min(1120px, 100vw)" placement="right" class="document-history-drawer" :style="themeVariables" @update:show="showHistory = $event">
      <NDrawerContent closable :native-scrollbar="false">
        <template #header><div class="history-drawer-title"><span>文档历史</span><span>{{editor.document?.name}}</span></div></template>
        <DocumentHistory v-if="showHistory && session.user && editor.document && access?.actions.includes('history_read')" :key="editor.document.id + session.epoch" :client="client" :object-id="editor.document.id" :editor="editor" :documents="documents" :can-restore="editable" @failure="failure($event, session.epoch)" @restored="notify('所选历史正文已恢复。', 'success')" />
      </NDrawerContent>
    </NDrawer>
    <FileActions :show="files.visible.value" :name="files.name.value" :action="files.action.value" :busy="fileBusy || documents.busy" :private-document="files.privateDocument.value" :can-create-private="['editor', 'admin', 'owner'].includes(session.workspaceRole ?? '')" :error="files.error.value" @update:private-document="files.privateDocument.value = $event" @update:name="files.name.value = $event" @close="files.close" @submit="files.submit" />
  </div>
</template>
