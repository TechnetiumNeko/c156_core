<script setup lang="ts">
import type { EditorState } from '../state/editor.ts';
defineProps<{ editor: EditorState; busy: boolean }>();
const emit = defineEmits<{ latest: []; merge: [] }>();
</script>
<template><section v-if="editor.conflict || editor.latest || editor.comparisonDraft !== null"><p v-if="editor.conflict">文档已有新修订，草稿已保留。可读取最新正文后手动合并。</p><button v-if="editor.conflict" :disabled="busy || editor.paused" @click="emit('latest')">查看最新正文</button><template v-if="editor.latest"><label>最新正文<textarea readonly :value="editor.latest.content" /></label><button :disabled="busy || editor.paused || !!editor.saving" @click="emit('merge')">开始手动合并（以最新正文为基础）</button></template><label v-if="editor.comparisonDraft !== null">合并前的草稿（只读，可复制）<textarea readonly :value="editor.comparisonDraft" /></label></section></template>
