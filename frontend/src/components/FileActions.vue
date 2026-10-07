<script setup lang="ts">
import { NModal, NInput, NFormItem, NButton } from 'naive-ui';
defineProps<{show: boolean; name: string; action: string; busy: boolean}>();
const emit = defineEmits<{'update:name': [name: string]; close: []; submit: []}>();
</script>
<template><NModal :show="show" preset="card" :title="action === 'rename' ? '重命名' : action === 'folder' ? '新建文件夹' : '新建文档'" class="max-w-md w-[calc(100vw_-_32px)]" :closable="!busy" :mask-closable="!busy" :close-on-esc="!busy" @update:show="value => {if (!value) emit('close')}">
  <form @submit.prevent="emit('submit')"><NFormItem label="名称" label-for="file-name"><NInput :value="name" :disabled="busy" :input-props="{id: 'file-name', required: true}" @update:value="emit('update:name', $event)" /></NFormItem><div class="toolbar-row justify-end"><NButton :disabled="busy" @click="emit('close')">取消</NButton><NButton type="primary" attr-type="submit" :loading="busy" :disabled="busy || !name.trim()">{{action === 'rename' ? '保存名称' : '创建'}}</NButton></div></form>
</NModal></template>
