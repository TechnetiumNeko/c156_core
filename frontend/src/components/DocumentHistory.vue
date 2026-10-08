<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { NButton, NAlert } from 'naive-ui';
import { ApiError, type ApiClient } from '../api/client.ts';
import type { DocumentSession } from '../state/document-session.ts';
import type { EditorState } from '../state/editor.ts';
import { useDocumentHistory } from '../composables/useDocumentHistory.ts';
const props = defineProps<{ client: ApiClient; objectId: string; editor?: EditorState; documents?: DocumentSession; canRestore?: boolean }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const history = useDocumentHistory(props.client);
const { revisions, selected, cursor, head, diff, busy } = history;
const expected = ref<string | null>(null); const choosing = ref(false); const working = ref(false);
const blocked = computed(() => working.value || busy.value || !!props.documents?.pending || !!props.documents?.busy || !!props.editor?.conflict || !!props.editor?.paused || (props.documents && !['none', 'comparison'].includes(props.documents.recovery.kind)));
let generation = 0;
watch(() => props.objectId, () => { generation++; history.reset(); expected.value = null; choosing.value = false; void run(() => history.list(props.objectId)); }, { immediate: true });
onUnmounted(() => { generation++; history.reset(); });
async function run(action: () => Promise<void>) { try { await action(); } catch (error) { emit('failure', error); } }
async function select(revision: string) { expected.value = null; choosing.value = false; await run(() => history.view(props.objectId, revision)); }
async function preview() {
  const revision = selected.value?.revision_id; if (!revision || blocked.value) return;
  if (props.editor?.hasUnsavedWork) { choosing.value = true; return; }
  const ticket = generation; const id = props.objectId; working.value = true; expected.value = null;
  try {
    const current = await props.client.readDocument(id);
    if (ticket !== generation) return;
    if (current.document.revision_id !== props.editor?.revision) {
      // Expose a comparison, but do not adopt the new base or replace the buffer.
      if (props.editor?.setLatest(current.document)) props.editor.conflict = true;
      throw new ApiError('conflict', '当前版本已变化，请先处理最新正文比较。');
    }
    await history.compare(id, revision, current.document.revision_id);
    if (ticket === generation) expected.value = current.document.revision_id;
  } catch (error) { emit('failure', error); }
  finally { if (ticket === generation) working.value = false; }
}
async function resolveDraft(save: boolean) {
  if (!props.documents || blocked.value) return;
  const ticket = generation; working.value = true;
  try {
    if (save) await props.documents.save();
    else if (window.confirm('明确放弃当前未保存稿和合并参考？请先复制需要保留的文字。')) await props.documents.discardDraft();
    else return;
    if (ticket !== generation) return;
    choosing.value = false;
  } catch (error) { emit('failure', error); return; }
  finally { if (ticket === generation) working.value = false; }
  if (ticket === generation && !props.editor?.hasUnsavedWork && !blocked.value) await preview();
}
async function restore() {
  if (!props.documents || !selected.value || !expected.value || blocked.value) return;
  const source = selected.value.revision_id; const base = expected.value;
  if (!window.confirm('将所选历史正文保存为当前正文？现有修订仍会保留。')) return;
  expected.value = null;
  await run(async () => { await props.documents!.restore(source, base); await history.list(props.objectId); });
}
</script>
<template>
  <section class="history-panel" aria-label="文档历史">
    <div class="toolbar-row justify-between"><h2 class="section-title">文档历史</h2><NButton size="small" :disabled="busy || working" @click="run(() => history.list(objectId))">刷新历史</NButton></div>
    <p class="muted-copy">查看和比较不会替换正在编辑的正文。操作者表示执行保存的人。</p>
    <ol class="history-list"><li v-for="item in revisions" :key="item.revision_id"><NButton text :disabled="busy || working" @click="select(item.revision_id)">{{item.created_at}}，{{item.actor_display_name ?? '未知操作者'}}，{{({save:'保存', restore:'恢复', import:'导入', unknown:'来源未知'})[item.source_kind]}}</NButton></li></ol>
    <p v-if="!busy && !revisions.length" class="muted-copy">没有可显示的历史。</p>
    <NButton v-if="cursor" size="small" :disabled="busy" @click="run(() => history.list(objectId, true))">加载更早修订</NButton>
    <div v-if="selected" class="history-detail"><h3>所选历史正文</h3><textarea class="copy-text" readonly :value="selected.content" aria-label="所选历史正文，可复制" />
      <div class="toolbar-row"><NButton :disabled="busy || working" @click="expected = null; run(() => history.compare(objectId, selected!.revision_id, head))">与列表起始版本比较</NButton><NButton v-if="canRestore" :disabled="blocked" @click="preview">比较并准备恢复</NButton></div>
    </div>
    <NAlert v-if="choosing" type="warning" class="mt-3">请先处理未保存稿，再重新比较恢复内容。<div class="toolbar-row mt-3"><NButton :disabled="blocked" @click="resolveDraft(true)">保存当前稿后重新比较</NButton><NButton :disabled="blocked" @click="resolveDraft(false)">放弃当前稿后重新比较</NButton><NButton @click="choosing = false; expected = null">取消恢复</NButton></div></NAlert>
    <pre v-if="diff !== null" class="history-diff" aria-label="只读行级差异">{{diff || '正文没有差异。'}}</pre>
    <div v-if="expected" class="toolbar-row"><NButton type="primary" :disabled="blocked || !!editor?.hasUnsavedWork" @click="restore">确认恢复所选正文</NButton><NButton @click="expected = null">取消恢复</NButton></div>
  </section>
</template>
