<script setup lang="ts">
import { computed, markRaw, onMounted, onUnmounted, reactive, ref } from 'vue';
import { ApiClient, ApiError } from './api/client.ts';
import type { Access } from './api/types.ts';
import { EditorState } from './state/editor.ts';
import { SessionState } from './state/session.ts';
import { DirectoryState } from './state/directory.ts';
import LoginPanel from './components/LoginPanel.vue';
import DirectoryTree from './components/DirectoryTree.vue';
import DocumentEditor from './components/DocumentEditor.vue';
const client = markRaw(new ApiClient());
const editor = reactive(new EditorState()) as EditorState;
const session = reactive(new SessionState(client, editor));
const directory = reactive(new DirectoryState(client)) as DirectoryState;
const busy = ref(false); const loadingDocument = ref(false); const loadingLatest = ref(false);
const message = ref(''); const ready = ref(false); const access = ref<Access | null>(null);
const blockedSave = ref(false);
const accessRefreshFailed = ref(false); const loadingAccess = ref(false);
let accessRequest = 0;
const editable = computed(() => !!access.value?.actions.includes('edit') && !access.value.frozen);
const status = computed(() => editor.paused ? '会话已暂停' : editor.saving ? '保存中，可继续输入' : editor.uncertainSave ? '保存结果未确认，草稿已保留' : blockedSave.value ? '保存受阻，草稿已保留' : editor.conflict ? '修订冲突，草稿已保留' : loadingAccess.value ? '正在确认文档权限' : accessRefreshFailed.value ? '文档权限读取失败，草稿已保留' : !editable.value ? '只读' : editor.dirty ? '未保存' : editor.comparisonDraft !== null ? '合并参考仍保留，请修改并保存' : '已保存');
function syncDirectory() { directory.setRoot(session.root, session.user?.id ?? null); }
async function failure(error: unknown, epoch: number) {
  if (epoch !== session.epoch || (error instanceof ApiError && error.code === 'stale')) return;
  message.value = error instanceof Error ? error.message : '请求失败，请重试';
  if (error instanceof ApiError && error.status === 401 && session.user) {
    directory.reset();
    busy.value = true;
    try { await session.expire(); ready.value = true; }
    catch (recovery) { message.value = recovery instanceof Error ? recovery.message : '会话恢复失败，请重试'; ready.value = false; }
    finally { busy.value = false; }
    message.value = '会话已失效，草稿已保留。请重新登录。';
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
  if (!editable.value) return;
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
function beforeUnload(event: BeforeUnloadEvent) { if (editor.shouldWarnBeforeUnload) { event.preventDefault(); event.returnValue = ''; } }
function keydown(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void save(); } }
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); window.addEventListener('keydown', keydown); void bootstrap(); });
onUnmounted(() => { window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('keydown', keydown); });
</script>
<template>
  <main>
    <h1>共享文档</h1>
    <header>
      <template v-if="session.user">
        <span>{{ session.user.display_name }}</span>
        <button :disabled="busy || loadingAccess || !!editor.saving || loadingDocument" @click="logout">退出登录</button>
      </template>
      <LoginPanel v-else-if="!session.blocked" :busy="busy" :ready="ready && session.initialized" @login="login" />
      <template v-else>
        <p>已登录另一个账号。原账号草稿仍保留，请先复制，或明确丢弃后继续。</p>
        <button :disabled="busy" @click="acceptPending">丢弃原草稿并继续</button>
      </template>
    </header>
    <p v-if="busy" role="status">正在处理…</p>
    <p v-if="message" role="alert">{{ message }}</p>
    <button v-if="!ready && !busy" @click="bootstrap">重新加载会话</button>
    <p v-if="ready && !session.initialized">数据库尚未初始化，请联系管理员。</p>
    <div class="workspace">
      <aside v-if="session.user">
        <h2>目录</h2>
        <DirectoryTree v-if="directory.root" :node="directory.root" :state="directory"
          :selected="editor.document?.id ?? null" :disabled="busy || loadingAccess || !!editor.saving || loadingDocument"
          @toggle="toggle" @select="select" />
        <p v-else>没有可访问的根目录。</p>
      </aside>
      <article>
        <p v-if="loadingDocument">正在读取文档…</p>
        <button v-if="accessRefreshFailed && session.user && !editor.paused && editor.document"
          :disabled="busy || loadingAccess || loadingDocument" @click="refreshDocumentAccess">重新读取文档权限（保留草稿）</button>
        <DocumentEditor :editor="editor" :editable="editable" :status="status" :busy="busy || loadingLatest || loadingAccess"
          @edit="editor.edit($event)" @save="save" @latest="latest" @merge="merge" />
      </article>
    </div>
  </main>
</template>
