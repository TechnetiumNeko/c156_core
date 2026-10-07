<script setup lang="ts">
import type { Node } from '../api/types.ts';
import type { DirectoryState } from '../state/directory.ts';
defineProps<{ node: Node; state: DirectoryState; disabled: boolean; selected: string | null }>();
const emit = defineEmits<{ toggle: [id: string]; select: [id: string] }>();
</script>
<template><ul><li><button :disabled="disabled" :aria-expanded="node.kind === 'folder' ? !!state.expanded[node.id] : undefined" :aria-current="selected === node.id ? 'page' : undefined" @click="node.kind === 'folder' ? emit('toggle', node.id) : emit('select', node.id)">{{ node.kind === 'folder' ? (state.expanded[node.id] ? '− ' : '+ ') : '' }}{{ node.name }}</button><template v-if="state.expanded[node.id]"><p v-if="state.loading[node.id]">读取中…</p><p v-if="state.errors[node.id]" role="alert">{{ state.errors[node.id] }} <button :disabled="disabled" @click="emit('toggle', node.id)">收起</button></p><p v-if="state.children[node.id]?.length === 0">空目录</p><DirectoryTree v-for="child in state.children[node.id]" :key="child.id" :node="child" :state="state" :disabled="disabled" :selected="selected" @toggle="emit('toggle', $event)" @select="emit('select', $event)" /></template></li></ul></template>
