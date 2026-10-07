<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { Compartment, EditorState } from '@codemirror/state';
import { EditorView, keymap, placeholder } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { markdown } from '@codemirror/lang-markdown';
import { GFM } from '@lezer/markdown';
import { syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language';
import { livePreview } from '../editor/livePreview.ts';
import { format, type Format } from '../editor/commands.ts';
const props = defineProps<{modelValue: string; documentId: string; readonly: boolean; sourceMode: boolean}>();
const emit = defineEmits<{'update:modelValue': [text: string]}>();
const host = ref<HTMLElement>(); let view: EditorView | undefined; let syncing = false;
const permissions = new Compartment(); const presentation = new Compartment();
const mode = () => props.sourceMode ? syntaxHighlighting(defaultHighlightStyle) : livePreview;
const rights = () => [EditorState.readOnly.of(props.readonly), EditorView.editable.of(!props.readonly)];
function makeState() {
  return EditorState.create({doc: props.modelValue, extensions: [markdown({extensions: GFM}), history(), keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]), EditorView.lineWrapping, placeholder('开始写作，支持 Markdown 格式…'), permissions.of(rights()), presentation.of(mode()), EditorView.contentAttributes.of({'aria-label': '文档正文', spellcheck: 'false'}), EditorView.updateListener.of(update => {if (update.docChanged && !syncing) emit('update:modelValue', update.state.doc.toString());}), EditorView.domEventHandlers({compositionend: () => {setTimeout(() => {if (view) {syncContent(); view.dispatch({});}}, 0);}})]});
}
function syncContent() {
  if (!view || view.composing || view.state.doc.toString() === props.modelValue) return;
  syncing = true;
  view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: props.modelValue}});
  syncing = false;
}
onMounted(() => {if (host.value) view = new EditorView({state: makeState(), parent: host.value});});
onBeforeUnmount(() => {view?.destroy(); view = undefined;});
watch(() => props.modelValue, syncContent);
watch(() => props.documentId, () => {if (view) view.setState(makeState());});
watch(() => props.readonly, () => view?.dispatch({effects: permissions.reconfigure(rights())}));
watch(() => props.sourceMode, () => view?.dispatch({effects: presentation.reconfigure(mode())}));
defineExpose({format: (kind: Format) => {if (view && !props.readonly) format(view, kind);}});
</script>
<template><div ref="host" class="markdown-editor" :class="{'is-readonly': readonly, 'source-mode': sourceMode}" /></template>
