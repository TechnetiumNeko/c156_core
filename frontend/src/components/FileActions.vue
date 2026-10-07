<script setup lang="ts">
import { NModal, NInput, NFormItem, NButton, NCheckbox, NAlert } from 'naive-ui';
defineProps<{show: boolean; name: string; action: string; busy: boolean; privateDocument: boolean; canCreatePrivate: boolean; error: string}>();
const emit = defineEmits<{'update:name': [name: string]; 'update:privateDocument': [value: boolean]; close: []; submit: []}>();
</script>
<template><NModal :show="show" preset="card" :title="action === 'rename' ? '重命名' : action === 'folder' ? '新建文件夹' : '新建文档'" class="max-w-md w-[calc(100vw_-_32px)]" :closable="!busy" :mask-closable="!busy" :close-on-esc="!busy" @update:show="value => {if (!value) emit('close')}">
  <form @submit.prevent="emit('submit')"><NAlert v-if="error" type="error" role="alert" class="mb-4">{{error}}</NAlert><NFormItem label="名称" label-for="file-name"><NInput :value="name" :disabled="busy" :input-props="{id: 'file-name', required: true}" @update:value="emit('update:name', $event)" /></NFormItem><div v-if="action === 'document' && canCreatePrivate" class="mb-5"><NCheckbox :checked="privateDocument" :disabled="busy" @update:checked="emit('update:privateDocument', $event)">创建私密文档</NCheckbox><p class="muted-copy mt-2 mb-0">私密文档仅你和工作区管理员可访问；仍需具有所在目录的访问权限。</p></div><div class="toolbar-row justify-end"><NButton :disabled="busy" @click="emit('close')">取消</NButton><NButton type="primary" attr-type="submit" :loading="busy" :disabled="busy || !name.trim()">{{action === 'rename' ? '保存名称' : '创建'}}</NButton></div></form>
</NModal></template>
