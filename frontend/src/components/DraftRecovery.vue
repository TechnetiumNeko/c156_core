<script setup lang="ts">
import { computed, ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import type { DocumentSession } from '../state/document-session.ts';
import type { EditorState } from '../state/editor.ts';
const props = defineProps<{documents: DocumentSession; editor: EditorState; authenticated: boolean; logoutFailed: boolean}>();
const emit = defineEmits<{failure: [error: unknown]; forceLogout: []}>();
const copying = ref(false);
const localOnly = computed(() => props.documents.recovery.kind === 'local-only');
const disabled = computed(() => props.documents.busy || !props.documents.leaseHeld || !props.authenticated || props.editor.paused);
const text = computed(() => localOnly.value ? props.documents.localOnlyContent ?? '' : props.editor.draft);
async function run(action: () => Promise<void>) {try {await action();} catch (error) {emit('failure', error);} }
function dismissComparison() {
  if (window.confirm('移除合并前的只读参考？请确认已合并或复制需要的文字，或决定放弃这份参考。移除后无法从本机草稿找回；当前编辑稿及其本机记录会保留。')) props.documents.dismissComparison();
}
async function discard() {
  const warning = localOnly.value ? '删除本机正文及待确认记录？这不会取消或撤销已发送的保存或恢复；请求可能已经提交或稍后完成。删除后可能无法确认该请求。请先复制需要保留的文字。' : '明确丢弃本机草稿？请先复制需要保留的文字。';
  if (window.confirm(warning)) await run(() => props.documents.discardDraft());
}
</script>
<template><section v-if="editor.document || localOnly || documents.localStatus.kind === 'error'" class="draft-status" aria-label="本机草稿与保存状态">
  <p role="status" class="muted-copy">{{({idle:'本机尚无新的存储记录',writing:'正在写入本机',stored:'本机已存',readonly:'此页只读',error:'本机未存好'})[documents.localStatus.kind]}}</p>
  <NAlert v-if="documents.localStatus.kind === 'error' || documents.localStatus.kind === 'readonly'" type="warning">{{documents.localStatus.message}}</NAlert>
  <NAlert v-if="documents.operationError" type="warning" class="mt-3">本次操作未完成：{{documents.operationError}}。{{documents.pending ? '原请求仍待确认，请查询结果或重试同一请求。' : '请处理原因后再保存。'}}</NAlert>
  <NAlert v-if="localOnly" type="warning" class="mt-3">当前无法读取此文档。这里只提供原账号的本机文字，不会读取服务器历史或保存正文。</NAlert>
  <div v-if="documents.recovery.kind === 'draft'" class="mt-3"><p>发现本机草稿。{{documents.recovery.baseStale ? '服务器已有新版本，继续后需要比较。' : '请选择继续编辑或明确丢弃。'}}</p><div class="toolbar-row"><NButton :disabled="disabled" @click="run(() => documents.continueDraft())">继续本机草稿</NButton><NButton :disabled="disabled" @click="discard">丢弃本机草稿</NButton></div></div>
  <div v-if="documents.pending && !localOnly" class="mt-3"><p>保存或恢复结果待确认。查询暂未找到结果时，仍需保留原请求。</p><div class="toolbar-row"><NButton :disabled="disabled" @click="run(() => documents.reconcilePending())">查询操作结果</NButton><NButton :disabled="disabled" @click="run(() => documents.retryPending())">重试同一请求</NButton></div></div>
  <div v-if="editor.comparisonDraft !== null" class="mt-3"><p>合并前的参考仅保留在此页面。离开前请合并或复制需要的文字，再明确移除参考。</p><NButton @click="dismissComparison">移除合并前参考</NButton></div>
  <div class="toolbar-row mt-3"><NButton @click="copying = !copying">显示可复制文字</NButton><NButton v-if="documents.localStatus.kind === 'error'" :disabled="documents.busy" @click="run(() => documents.flush())">重试本机存储</NButton><NButton v-if="localOnly && documents.localOnlyContent !== null" :disabled="disabled" @click="discard">删除本机记录</NButton><NButton v-if="logoutFailed && documents.localStatus.kind === 'error'" @click="emit('forceLogout')">确认可能丢失未落盘文字后退出</NButton></div>
  <textarea v-if="copying || localOnly" class="copy-text" readonly :value="text" aria-label="本机文字，可选择复制" />
</section></template>
