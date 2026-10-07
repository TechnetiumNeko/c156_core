<script setup lang="ts">
import type { EditorState } from '../state/editor.ts';
import ConflictPanel from './ConflictPanel.vue';
defineProps<{ editor: EditorState; editable: boolean; status: string; busy: boolean }>();
const emit = defineEmits<{ edit: [text: string]; save: []; latest: []; merge: [] }>();
</script>
<template><section v-if="editor.document"><h2>{{ editor.document.name }}</h2><p v-if="editor.paused">会话已暂停。原草稿只读，可选择并复制；同一账号重新登录后可继续。</p><label>文档正文<textarea :value="editor.draft" :readonly="editor.paused || !editable" @input="emit('edit', ($event.target as HTMLTextAreaElement).value)" /></label><div><button :disabled="!editable || editor.paused || !!editor.saving || editor.conflict || (!editor.dirty && !editor.uncertainSave)" @click="emit('save')">保存</button> <span role="status">{{ status }}</span></div><ConflictPanel :editor="editor" :busy="busy" @latest="emit('latest')" @merge="emit('merge')" /></section><p v-else>从目录选择文档。</p></template>
