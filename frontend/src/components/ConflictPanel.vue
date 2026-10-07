<script setup lang="ts">
import { NAlert, NButton, NInput } from 'naive-ui';
import type { EditorState } from '../state/editor.ts';
defineProps<{editor: EditorState; busy: boolean}>();
const emit = defineEmits<{latest: []; merge: []}>();
</script>
<template><section v-if="editor.conflict || editor.latest || editor.comparisonDraft !== null" class="conflict-panel">
  <NAlert v-if="editor.conflict" type="warning" role="alert" class="mb-4">文档已有新修订，草稿已保留。读取最新正文后可以手动合并。</NAlert>
  <NButton v-if="editor.conflict" :disabled="busy || editor.paused || !!editor.saving" @click="emit('latest')">读取最新正文</NButton>
  <div v-if="editor.latest" class="mt-5"><label for="latest-content" class="block mb-2 font-medium">最新正文</label><NInput :input-props="{id: 'latest-content'}" type="textarea" readonly :value="editor.latest.content" :autosize="{minRows: 6, maxRows: 16}" /><NButton class="mt-3" :disabled="busy || editor.paused || !!editor.saving" @click="emit('merge')">以最新正文开始手动合并</NButton></div>
  <div v-if="editor.comparisonDraft !== null" class="mt-5"><label for="comparison-draft" class="block mb-2 font-medium">合并前的草稿（只读，可复制）</label><NInput :input-props="{id: 'comparison-draft'}" type="textarea" readonly :value="editor.comparisonDraft" :autosize="{minRows: 6, maxRows: 16}" /></div>
</section></template>
