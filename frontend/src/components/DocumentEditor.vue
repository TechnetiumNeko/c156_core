<script setup lang="ts">
import { ref, computed, defineAsyncComponent } from 'vue';
import { NButton, NDropdown, NTag, NAlert } from 'naive-ui';
import type { EditorState } from '../state/editor.ts';
import type { Format } from '../editor/commands.ts';
import ConflictPanel from './ConflictPanel.vue';
const MarkdownEditor = defineAsyncComponent(() => import('./MarkdownEditor.vue'));
const MarkdownPreview = defineAsyncComponent(() => import('./MarkdownPreview.vue'));
const props = defineProps<{editor: EditorState; editable: boolean; canSave: boolean; status: string; busy: boolean}>();
const emit = defineEmits<{edit: [text: string]; save: []; latest: []; merge: []}>();
const mode = ref<'live' | 'source' | 'read'>('live'); const input = ref<{format: (kind: Format) => void}>();
const formats: {key: Format; label: string}[] = [{key: 'heading', label: '标题'}, {key: 'bold', label: '粗体'}, {key: 'italic', label: '斜体'}, {key: 'list', label: '列表'}, {key: 'quote', label: '引用'}, {key: 'link', label: '链接'}, {key: 'code', label: '代码'}];
const read = computed(() => mode.value === 'read' || (!props.editable && !props.editor.paused));
const options = [{key: 'live', label: '实时预览编辑'}, {key: 'source', label: '显示完整源码'}, {key: 'read', label: '阅读渲染结果'}];
</script>
<template><section v-if="editor.document" class="document-surface">
  <div class="document-header"><div class="min-w-0"><h1 class="m-0 text-xl font-semibold break-words">{{editor.document.name}}</h1><div class="toolbar-row mt-2"><NTag size="small" :bordered="false" :type="editor.conflict || editor.uncertainSave ? 'warning' : editor.dirty ? 'default' : 'success'" role="status">{{status}}</NTag><span class="muted-copy">手动保存，Ctrl / ⌘ S</span></div></div><div class="toolbar-row"><NDropdown :options="options" @select="value => mode = value"><NButton size="small">{{mode === 'source' ? '源码' : read ? '阅读' : '显示方式'}} ▾</NButton></NDropdown><NButton type="primary" :loading="!!editor.saving" :disabled="!canSave || busy || editor.paused || editor.conflict || !editor.dirty" @click="emit('save')">保存</NButton></div></div>
  <NAlert v-if="editor.paused" type="warning" :bordered="false" class="mx-5 mt-4" role="alert">会话已暂停。草稿仍保留，可选择复制；使用同一账号登录后继续编辑。</NAlert>
  <div v-show="!read" class="format-toolbar"><NButton v-for="item in formats" :key="item.key" quaternary size="small" :disabled="!editable || editor.paused" @mousedown.prevent @click="input?.format(item.key)">{{item.label}}</NButton><span class="muted-copy ml-auto">{{mode === 'source' ? 'Markdown 源码' : '点击正文直接编辑'}}</span></div>
  <MarkdownEditor v-show="!read" ref="input" :model-value="editor.draft" :document-id="editor.document.id" :readonly="editor.paused || !editable" :source-mode="mode === 'source'" @update:model-value="emit('edit', $event)" />
  <MarkdownPreview v-if="read" :source="editor.draft" />
  <ConflictPanel :editor="editor" :busy="busy" @latest="emit('latest')" @merge="emit('merge')" />
</section><div v-else class="editor-empty"><div class="empty-document" aria-hidden="true">▤</div><h2 class="section-title">选择一篇文档</h2><p class="muted-copy">从左侧目录打开文档，或在文件夹的操作菜单中新建。</p></div></template>
