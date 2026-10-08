<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { NButton } from 'naive-ui';
import type { ApiClient } from '../api/client.ts';
import type { DeletedDocumentSummary } from '../api/types.ts';
import DocumentHistory from './DocumentHistory.vue';
const props = defineProps<{client: ApiClient}>();
const emit = defineEmits<{failure: [error: unknown]}>();
const items = ref<DeletedDocumentSummary[]>([]); const cursor = ref<string | null>(null); const selected = ref<string | null>(null); const busy = ref(false); let active = true;
async function load(more = false) {
  busy.value = true;
  try { const result = await props.client.listDeletedDocuments(more ? cursor.value ?? undefined : undefined); if (active) {items.value = more ? [...items.value, ...result.documents] : result.documents; cursor.value = result.next_cursor;} }
  catch (error) { if (active) emit('failure', error); }
  finally { if (active) busy.value = false; }
}
onMounted(() => void load()); onUnmounted(() => { active = false; });
</script>
<template><section class="history-panel"><div class="toolbar-row justify-between"><h2 class="section-title">已删除文档</h2><NButton :disabled="busy" @click="load()">刷新列表</NButton></div><p class="muted-copy">可以查看和复制保留的正文历史，暂不支持恢复到目录。</p><ul class="history-list"><li v-for="item in items" :key="item.object_id"><NButton text @click="selected = item.object_id">{{item.path || item.name}}</NButton></li></ul><p v-if="!busy && !items.length">没有已删除文档。</p><NButton v-if="cursor" :disabled="busy" @click="load(true)">加载更多</NButton><DocumentHistory v-if="selected" :key="selected" :client="client" :object-id="selected" @failure="emit('failure', $event)" /></section></template>
