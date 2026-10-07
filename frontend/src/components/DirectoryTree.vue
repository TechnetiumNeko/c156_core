<script setup lang="ts">
import { computed, ref } from 'vue';
import { NButton, NDropdown } from 'naive-ui';
import type { Node, Access } from '../api/types.ts';
import type { DirectoryState } from '../state/directory.ts';
import type { FileAction } from '../composables/useFileOperations.ts';
const props = defineProps<{node: Node; access?: Access | null; state: DirectoryState; disabled: boolean; selected: string | null}>();
const emit = defineEmits<{toggle: [id: string]; select: [id: string]; action: [action: FileAction, node: Node]; failure: [error: unknown]}>();
const menu = ref(false); const x = ref(0); const y = ref(0);
const options = computed(() => {
  const actions = props.access?.actions ?? []; const list: {label: string; key: FileAction; disabled?: boolean}[] = [];
  if (props.node.kind === 'folder' && actions.includes('create')) list.push({label: '新建文档', key: 'document'}, {label: '新建文件夹', key: 'folder'});
  if (props.node.kind === 'document') list.push({label: actions.includes('edit') ? '打开编辑' : '打开文档', key: 'edit'});
  if (actions.includes('rename')) list.push({label: '重命名', key: 'rename'});
  if (actions.includes('delete')) list.push({label: '删除', key: 'delete'});
  return list.map(item => ({...item, disabled: props.disabled}));
});
function show(event: MouseEvent) {if (!options.value.length || props.disabled) return; x.value = event.clientX; y.value = event.clientY; menu.value = true;}
async function retry() {try {await props.state.loadChildren(props.node.id);} catch (error) {emit('failure', error);}}
function choose(key: FileAction) {menu.value = false; emit('action', key, props.node);}
</script>
<template><ul class="directory-list"><li>
  <div class="directory-row" :class="{'is-selected': selected === node.id}" @contextmenu.prevent="show">
    <button class="directory-entry" :disabled="disabled" :aria-expanded="node.kind === 'folder' ? !!state.expanded[node.id] : undefined" :aria-current="selected === node.id ? 'page' : undefined" @click="node.kind === 'folder' ? emit('toggle', node.id) : emit('select', node.id)"><span class="directory-symbol" aria-hidden="true">{{node.kind === 'folder' ? (state.expanded[node.id] ? '▾' : '▸') : '▤'}}</span><span class="truncate">{{node.name}}</span></button>
    <NButton v-if="options.length" text size="tiny" :disabled="disabled" :aria-label="node.name + '的操作'" class="directory-more" @click="show">•••</NButton>
  </div>
  <NDropdown trigger="manual" :show="menu" :x="x" :y="y" :options="options" @select="choose" @clickoutside="menu = false" />
  <div v-if="state.expanded[node.id]" class="directory-children">
    <p v-if="state.loading[node.id]" class="muted-copy pl-3" role="status">正在读取…</p>
    <p v-if="state.errors[node.id]" class="directory-error" role="alert">目录读取失败。<NButton text size="small" :disabled="disabled" @click="retry">重试</NButton></p>
    <p v-if="state.children[node.id]?.length === 0" class="muted-copy pl-3">空目录</p>
    <DirectoryTree v-for="child in state.children[node.id]" :key="child.id" :node="child" :access="child.access" :state="state" :disabled="disabled" :selected="selected" @toggle="emit('toggle', $event)" @select="emit('select', $event)" @action="(action, node) => emit('action', action, node)" @failure="emit('failure', $event)" />
  </div>
</li></ul></template>
