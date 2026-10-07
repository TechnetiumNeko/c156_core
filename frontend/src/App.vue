<script setup lang="ts">
import { computed, markRaw, onMounted, onUnmounted, reactive, ref, watch, defineAsyncComponent } from 'vue';
import { NButton, NAlert, NTag } from 'naive-ui';
import { useConfirm } from './composables/useConfirm.ts';
import { useFileOperations } from './composables/useFileOperations.ts';
import { errorText } from './composables/errors.ts';
const AccountPage = defineAsyncComponent(() => import('./pages/AccountPage.vue'));
const AdminUsersPage = defineAsyncComponent(() => import('./pages/AdminUsersPage.vue'));
const MembersPage = defineAsyncComponent(() => import('./pages/MembersPage.vue'));
import FileActions from './components/FileActions.vue';
import { ApiClient, ApiError } from './api/client.ts';
import type { Access } from './api/types.ts';
import { EditorState } from './state/editor.ts';
import { SessionState } from './state/session.ts';
import { DirectoryState } from './state/directory.ts';
import LoginPanel from './components/LoginPanel.vue';
import DirectoryTree from './components/DirectoryTree.vue';
import DocumentEditor from './components/DocumentEditor.vue';
const confirm = useConfirm();
const page = ref<'documents' | 'account' | 'admin' | 'members'>('documents');
const client = markRaw(new ApiClient());
const editor = reactive(new EditorState()) as EditorState;
const session = reactive(new SessionState(client, editor)) as SessionState;
const directory = reactive(new DirectoryState(client)) as DirectoryState;
const busy = ref(false); const loadingDocument = ref(false); const loadingLatest = ref(false);
const message = ref(''); const ready = ref(false); const access = ref<Access | null>(null);
const blockedSave = ref(false);
const accessRefreshFailed = ref(false); const loadingAccess = ref(false);
const files = useFileOperations(client, session, directory, editor, select, failure, confirm);
const fileBusy = files.busy;
const navigationBusy = computed(() => busy.value || loadingAccess.value || loadingDocument.value || !!editor.saving || fileBusy.value);
const managesWorkspace = computed(() => session.workspaceRole === 'admin' || session.workspaceRole === 'owner');
watch(() => session.epoch, () => files.reset());
watch(() => [session.user?.id, session.user?.site_admin, session.workspaceRole], () => {if (!session.user || (page.value === 'admin' && !session.user.site_admin) || (page.value === 'members' && !managesWorkspace.value)) page.value = 'documents';});
let accessRequest = 0;
const editable = computed(() => !!access.value?.actions.includes('edit') && !session.recoveryNeeded);
const status = computed(() => editor.paused ? '会话已暂停' : editor.saving ? '保存中，可继续输入' : editor.uncertainSave ? '保存结果未确认，草稿已保留' : blockedSave.value ? '保存受阻，草稿已保留' : editor.conflict ? '修订冲突，草稿已保留' : loadingAccess.value ? '正在确认文档权限' : accessRefreshFailed.value ? '文档权限读取失败，草稿已保留' : !editable.value ? '只读' : editor.dirty ? '未保存' : editor.comparisonDraft !== null ? '合并参考仍保留，请修改并保存' : '已保存');
function syncDirectory() { directory.setRoot(session.root, session.user?.id ?? null); }
async function failure(error: unknown, epoch: number) {
  if (epoch !== session.epoch || (error instanceof ApiError && error.code === 'stale')) return;
  message.value = errorText(error);
  if (error instanceof ApiError && error.status === 401 && session.user) {
    directory.reset();
    busy.value = true;
    try { await session.expire(); ready.value = true; }
    catch (recovery) { message.value = recovery instanceof Error ? recovery.message : '会话恢复失败，请重试'; ready.value = false; }
    finally { busy.value = false; }
    message.value = '会话已失效，草稿已保留。请重新登录。';
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
function discardWork() {
  if (editor.saving) return false;
  if (editor.hasUnsavedWork && !window.confirm('草稿、未确认的保存结果或合并参考仍未处理。取消可继续保留；确定将明确丢弃这些内容。请先复制需要保留的正文。')) return false;
  editor.setIdentity(session.user?.id ?? null, { discard: true }); access.value = null; blockedSave.value = false; accessRefreshFailed.value = false;
  return true;
}
async function logout() {
  if (!discardWork()) return;
  busy.value = true; message.value = ''; directory.reset();
  try { await session.logout(); syncDirectory(); ready.value = true; }
  catch (error) { await failure(error, session.epoch); syncDirectory(); }
  finally { busy.value = false; }
}
function acceptPending() {
  if (!window.confirm('确定丢弃原账号的草稿和合并参考，并进入新账号？请先复制需要保留的内容。')) return;
  if (session.acceptPending({ discard: true })) { access.value = null; blockedSave.value = false; accessRefreshFailed.value = false; syncDirectory(); }
}
async function toggle(id: string) {
  const epoch = session.epoch;
  try { await directory.toggle(id); } catch (error) { await failure(error, epoch); }
}
async function select(id: string) {
  if (editor.document?.id === id || editor.saving || !discardWork()) return;
  const ticket = editor.beginLoad(); const epoch = session.epoch;
  loadingDocument.value = true; message.value = '';
  try { const value = await client.readDocument(id); if (editor.finishLoad(ticket, value.document)) { access.value = value.access; blockedSave.value = false; } }
  catch (error) { await failure(error, epoch); }
  finally { loadingDocument.value = false; }
}
async function save() {
  if (!editable.value || fileBusy.value) return;
  const ticket = editor.beginSave(); if (!ticket) return;
  const epoch = session.epoch; message.value = ''; blockedSave.value = false;
  try { const value = await client.saveDocument(ticket); if (editor.saveSucceeded(ticket, value.document)) access.value = value.access; }
  catch (error) {
    const code = error instanceof ApiError ? error.code : 'network';
    if (editor.saveFailed(ticket, code)) blockedSave.value = error instanceof ApiError && (error.status === 403 || code === 'frozen');
    await failure(error, epoch);
  }
}
async function latest() {
  if (!editor.document || editor.paused) return;
  const ticket = editor.beginLatest(); const epoch = session.epoch; loadingLatest.value = true; message.value = '';
  try { const value = await client.readDocument(editor.document.id); if (editor.setLatest(value.document, ticket.epoch, ticket)) access.value = value.access; }
  catch (error) { await failure(error, epoch); }
  finally { loadingLatest.value = false; }
}
function merge() { if (window.confirm('以最新正文开始手动合并？当前草稿将保留在独立的只读参考区。')) { editor.startMerge(); blockedSave.value = false; } }
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
        <NButton v-if="managesWorkspace" :type="page === 'members' ? 'primary' : 'default'" quaternary :disabled="navigationBusy" @click="page = 'members'">成员管理</NButton>
      </nav>
      <div v-if="session.user" class="toolbar-row ml-auto"><span class="text-sm text-muted max-w-36 truncate">{{session.user.display_name}}</span><NButton size="small" :disabled="navigationBusy" @click="logout">退出</NButton></div>
    </header>
    <div v-if="message || busy || (!ready || session.recoveryNeeded) || (ready && !session.initialized) || session.blocked" class="app-notices">
      <NAlert v-if="message" type="warning" role="alert" class="mb-2">{{message}}</NAlert>
      <p v-if="busy" class="muted-copy m-0" role="status">正在处理…</p>
      <NButton v-if="(!ready || session.recoveryNeeded) && !busy" size="small" @click="bootstrap">重新加载会话（保留草稿）</NButton>
      <NAlert v-if="ready && !session.initialized" type="info">数据库尚未初始化，请联系管理员。</NAlert>
      <NAlert v-if="session.blocked" type="warning" role="alert">已登录另一个账号。原账号草稿仍保留，请先复制，或明确丢弃后继续。<NButton class="mt-3" :disabled="busy" @click="acceptPending">丢弃原草稿并继续</NButton></NAlert>
    </div>
    <main v-show="page === 'documents'" class="workbench" :class="{'anonymous-workbench': !session.user}">
      <aside v-if="session.user" class="directory-sidebar">
        <div class="toolbar-row justify-between mb-5"><h2 class="section-title">目录</h2><NButton v-if="session.root && session.rootAccess?.actions.includes('create')" size="small" :disabled="navigationBusy" @click="files.open('document', session.root)">新建文档</NButton></div>
        <DirectoryTree v-if="directory.root" :node="directory.root" :access="session.rootAccess" :state="directory" :selected="editor.document?.id ?? null" :disabled="navigationBusy" @toggle="toggle" @select="select" @action="files.open" @failure="failure($event, session.epoch)" />
        <p v-else class="muted-copy">没有可访问的根目录。请联系工作区管理员确认成员资格。</p>
        <p class="directory-help muted-copy">右键目录项或点击操作按钮，可新建、重命名和删除。</p>
      </aside>
      <aside v-else-if="!session.blocked" class="login-surface"><LoginPanel :client="client" :busy="busy" :ready="ready && !session.recoveryNeeded && session.initialized" @login="login" @failure="failure($event, session.epoch)" /></aside>
      <article class="editor-area" :class="{'anonymous-empty': !session.user && !editor.document}">
        <NAlert v-if="files.message.value" :type="files.messageType.value" class="mb-3" :role="files.messageType.value === 'error' ? 'alert' : 'status'">{{files.message.value}}</NAlert>
        <p v-if="loadingDocument" class="muted-copy" role="status">正在读取文档…</p>
        <NButton v-if="accessRefreshFailed && session.user && !editor.paused && editor.document" :disabled="navigationBusy" class="mb-3" @click="refreshDocumentAccess">重新读取文档权限（保留草稿）</NButton>
        <DocumentEditor :editor="editor" :editable="editable" :status="status" :busy="busy || loadingLatest || loadingAccess" @edit="editor.edit($event)" @save="save" @latest="latest" @merge="merge" />
      </article>
    </main>
    <template v-if="session.user">
      <AccountPage v-if="page === 'account'" :key="session.epoch" :client="client" :user="session.user" :workspace-role="session.workspaceRole" @profile="session.user = $event" @revoked="passwordRevoked" @failure="failure($event, session.epoch)" />
      <AdminUsersPage v-if="page === 'admin' && session.user.site_admin" :key="session.epoch" :client="client" :user="session.user" @profile="session.user = $event" @failure="failure($event, session.epoch)" />
      <MembersPage v-if="page === 'members' && managesWorkspace" :key="session.epoch" :client="client" :user="session.user" @refresh="bootstrap" @failure="failure($event, session.epoch)" />
    </template>
    <FileActions :show="files.visible.value" :name="files.name.value" :action="files.action.value" :busy="fileBusy || !!editor.saving" :private-document="files.privateDocument.value" :can-create-private="['editor', 'admin', 'owner'].includes(session.workspaceRole ?? '')" :error="files.error.value" @update:private-document="files.privateDocument.value = $event" @update:name="files.name.value = $event" @close="files.close" @submit="files.submit" />
  </div>
</template>
