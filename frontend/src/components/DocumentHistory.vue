<script setup lang="ts">
import { computed, defineAsyncComponent, h, onUnmounted, ref, watch } from 'vue';
import { NButton, NAlert, NSpin, NTabs, NTabPane, useMessage } from 'naive-ui';
import { ApiError, type ApiClient } from '../api/client.ts';
import type { DocumentSession } from '../state/document-session.ts';
import type { EditorState } from '../state/editor.ts';
import { useDocumentHistory } from '../composables/useDocumentHistory.ts';
import { useConfirm } from '../composables/useConfirm.ts';
const props = defineProps<{ client: ApiClient; objectId: string; editor?: EditorState; documents?: DocumentSession; canRestore?: boolean }>();
const emit = defineEmits<{ failure: [error: unknown]; restored: [] }>();
const MarkdownPreview = defineAsyncComponent(() => import('./MarkdownPreview.vue'));
const notices = useMessage();
const confirm = useConfirm();
const history = useDocumentHistory(props.client);
const { revisions, selected, cursor, head, diff, busy } = history;
const tab = ref<'read' | 'source' | 'diff'>('read');
const activeRevision = ref<string | null>(null); const error = ref('');
const comparison = ref<'browse' | 'restore' | null>(null);
const labels = {save: '保存', restore: '恢复', import: '导入', unknown: '历史记录'};
const diffLines = computed(() => (diff.value ?? '').split('\n').filter((line, index, lines) => index < lines.length - 1 || line !== ''));
function lineKind(line: string) {
  if (line.startsWith('--- ') || line.startsWith('+++ ') || line.startsWith('@@') || line.startsWith('\\')) return 'meta';
  return line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : 'context';
}
function datePart(value: string, time = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', time ? {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false} : {year: 'numeric', month: '2-digit', day: '2-digit'}).format(date);
}
const expected = ref<string | null>(null); const choosing = ref(false); const working = ref(false);
const blocked = computed(() => working.value || busy.value || !!props.documents?.pending || !!props.documents?.busy || !!props.editor?.conflict || !!props.editor?.paused || (props.documents && !['none', 'comparison'].includes(props.documents.recovery.kind)));
let generation = 0;
watch(() => props.objectId, () => { generation++; history.reset(); expected.value = null; choosing.value = false; activeRevision.value = null; tab.value = 'read'; void refresh(); }, { immediate: true });
onUnmounted(() => { generation++; history.reset(); });
async function run(action: () => Promise<void>) {
  error.value = '';
  try { await action(); }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '历史读取失败，请重试。'; emit('failure', caught); }
}
async function refresh(more = false) {
  if (busy.value || working.value) return;
  const ticket = generation;
  await run(async () => {
    await history.list(props.objectId, more);
    if (ticket !== generation) return;
    expected.value = null; choosing.value = false; diff.value = null; comparison.value = null; if (tab.value === 'diff') tab.value = 'read';
    if (!selected.value && revisions.value[0]) await select(revisions.value[0].revision_id);
  });
}
async function select(revision: string) {
  expected.value = null; choosing.value = false; comparison.value = null; activeRevision.value = revision;
  if (tab.value === 'diff') tab.value = 'read';
  await run(() => history.view(props.objectId, revision));
}
async function changeTab(value: string) {
  tab.value = value as typeof tab.value;
  if (value !== 'diff' || diff.value !== null || !selected.value || busy.value || working.value) return;
  comparison.value = 'browse';
  await run(() => history.compare(props.objectId, selected.value!.revision_id, head.value));
}
async function copy() {
  if (!selected.value) return;
  await run(async () => {
    await navigator.clipboard.writeText(selected.value!.content);
    notices.success(() => h('span', {role: 'status'}, '历史正文已复制。'), {duration: 3200});
  });
}
async function preview() {
  const revision = selected.value?.revision_id; if (!revision || blocked.value) return;
  if (props.editor?.hasUnsavedWork) { choosing.value = true; return; }
  const ticket = generation; const id = props.objectId; working.value = true; expected.value = null; error.value = ''; diff.value = null;
  try {
    const current = await props.client.readDocument(id);
    if (ticket !== generation) return;
    if (current.document.revision_id !== props.editor?.revision) {
      // Expose a comparison, but do not adopt the new base or replace the buffer.
      if (props.editor?.setLatest(current.document)) props.editor.conflict = true;
      throw new ApiError('conflict', '当前版本已变化，请先处理最新正文比较。');
    }
    comparison.value = 'restore'; tab.value = 'diff';
    await history.compare(id, current.document.revision_id, revision);
    if (ticket === generation) expected.value = current.document.revision_id;
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '恢复预览未完成，请重试。'; emit('failure', caught); }
  finally { if (ticket === generation) working.value = false; }
}
async function resolveDraft(save: boolean) {
  if (!props.documents || blocked.value) return;
  const ticket = generation; working.value = true;
  try {
    if (save) await props.documents.save();
    else if (await confirm('放弃当前稿', '明确放弃当前未保存稿和合并参考？请先复制需要保留的文字。')) await props.documents.discardDraft();
    else return;
    if (ticket !== generation) return;
    choosing.value = false;
  } catch (error) { emit('failure', error); return; }
  finally { if (ticket === generation) working.value = false; }
  if (ticket === generation && !props.editor?.hasUnsavedWork && !blocked.value) await preview();
}
async function restore() {
  if (!props.documents || !selected.value || !expected.value || blocked.value || props.editor?.hasUnsavedWork) return;
  const source = selected.value.revision_id; const base = expected.value;
  expected.value = null; working.value = true;
  const ticket = generation;
  try {
    await run(async () => {
      await props.documents!.restore(source, base);
      if (ticket !== generation) return;
      if (!props.documents!.pending && !props.documents!.operationError) emit('restored');
      await history.list(props.objectId);
      if (ticket === generation) { diff.value = null; comparison.value = null; tab.value = 'read'; }
    });
  } finally { if (ticket === generation) working.value = false; }
}
</script>
<template>
  <section class="revision-browser" aria-label="文档历史" :aria-busy="busy || working">
    <header class="revision-browser-header">
      <p>查看过去保存的版本，正文可以复制或恢复。浏览历史不会改动当前草稿。</p>
      <NButton size="small" :disabled="busy || working" @click="refresh()">刷新</NButton>
    </header>
    <div v-if="error" class="revision-error" role="alert">{{error}}<NButton size="small" :disabled="busy || working" @click="refresh()">重新读取历史</NButton></div>
    <div class="revision-layout">
      <aside class="revision-timeline" aria-label="版本列表">
        <div class="revision-list-heading"><h3>保存记录</h3><span>{{revisions.length}}{{cursor ? '+' : ''}} 条</span></div>
        <ol class="revision-list">
          <li v-for="item in revisions" :key="item.revision_id">
            <button class="revision-entry" :class="{'is-active': activeRevision === item.revision_id}" :disabled="busy || working" :aria-current="activeRevision === item.revision_id ? 'true' : undefined" @click="select(item.revision_id)">
              <span class="revision-entry-top"><time :datetime="item.created_at" :title="new Date(item.created_at).toLocaleString()">{{datePart(item.created_at)}}</time><span v-if="item.revision_id === head" class="revision-current">当前版本</span></span>
              <span class="revision-entry-time">{{datePart(item.created_at, true)}}</span>
              <span class="revision-entry-bottom"><span class="revision-actor">{{item.actor_display_name ?? '未知操作者'}}</span><span class="revision-kind" :class="{'is-restore': item.source_kind === 'restore'}">{{labels[item.source_kind]}}</span></span>
            </button>
          </li>
        </ol>
        <p v-if="!busy && !revisions.length" class="revision-list-empty">暂无历史记录。</p>
        <NButton v-if="cursor" class="revision-load-more" size="small" :disabled="busy || working" @click="refresh(true)">加载更早记录</NButton>
      </aside>
      <div class="revision-reader">
        <div v-if="busy && !selected" class="revision-empty" role="status"><NSpin size="small" /><p>正在读取历史…</p></div>
        <template v-else-if="selected">
          <header class="revision-detail-header">
            <div><h3>{{selected.revision_id === head ? '当前版本' : '历史版本'}}</h3><p><time :datetime="selected.created_at">{{datePart(selected.created_at)}} {{datePart(selected.created_at, true)}}</time><span>{{selected.actor_display_name ?? '未知操作者'}}，{{labels[selected.source_kind]}}</span></p></div>
            <NButton size="small" :disabled="busy || working" @click="copy">复制正文</NButton>
          </header>
          <NTabs :value="tab" type="line" class="revision-tabs" :animated="false" @update:value="changeTab">
            <NTabPane name="read" tab="阅读" :disabled="working"><div class="revision-reading-surface"><MarkdownPreview v-if="selected.content" :source="selected.content" /><p v-else class="revision-content-empty">这个版本的正文为空。</p></div></NTabPane>
            <NTabPane name="source" tab="源码" :disabled="working"><textarea class="revision-source" readonly :value="selected.content" aria-label="所选历史版本的 Markdown 源码" spellcheck="false" /></NTabPane>
            <NTabPane name="diff" tab="差异" :disabled="busy || working">
              <div class="revision-comparison-caption"><strong>{{comparison === 'restore' ? '恢复预览：当前正文 → 所选版本' : '所选版本 → 当前版本（本次列表读取时）'}}</strong><div class="revision-diff-legend"><span class="is-added">+ 新增</span><span class="is-removed">− 删除</span></div></div>
              <div v-if="busy || working" class="revision-empty" role="status"><NSpin size="small" /><p>正在比较正文…</p></div>
              <div v-else-if="diff !== null && diff !== ''" class="revision-diff" aria-label="版本正文差异"><pre v-for="(line, index) in diffLines" :key="index" :class="'diff-' + lineKind(line)">{{line || ' '}}</pre></div>
              <div v-else class="revision-empty"><p>{{error ? '未能读取差异，请重新读取历史后重试。' : '这两个版本的正文一致。'}}</p></div>
            </NTabPane>
          </NTabs>
          <footer v-if="canRestore" class="revision-restore-bar">
            <template v-if="expected"><div><strong>确认使用这个版本的正文？</strong><p>恢复会生成一条新记录，已有历史仍会保留。</p></div><div class="toolbar-row"><NButton :disabled="working || busy" @click="expected = null">取消恢复</NButton><NButton type="primary" :disabled="blocked || !!editor?.hasUnsavedWork" @click="restore">确认恢复</NButton></div></template>
            <template v-else><p>{{selected.revision_id === head ? '这是当前服务器版本。' : '恢复前会先比较正文，并检查未保存的草稿。'}}</p><NButton :disabled="blocked || selected.revision_id === head" :loading="working" @click="preview">恢复此版本…</NButton></template>
          </footer>
          <NAlert v-if="choosing" type="warning" class="revision-draft-choice" :bordered="false"><strong>先处理未保存的草稿</strong><p>保存或放弃当前稿后，再查看恢复预览。</p><div class="toolbar-row"><NButton :disabled="blocked" @click="resolveDraft(true)">保存当前稿</NButton><NButton :disabled="blocked" @click="resolveDraft(false)">放弃当前稿</NButton><NButton @click="choosing = false; expected = null">取消</NButton></div></NAlert>
        </template>
        <div v-else class="revision-empty"><span class="revision-empty-mark" aria-hidden="true">↶</span><h3>{{revisions.length ? '选择一条保存记录' : '还没有可查看的历史'}}</h3><p>{{revisions.length ? '在左侧选择版本，查看正文或比较差异。' : '保存文档后，可以在这里查看过去的版本。'}}</p></div>
      </div>
    </div>
  </section>
</template>
