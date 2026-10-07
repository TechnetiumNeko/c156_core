<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { NButton, NInput, NSelect, NFormItem, NTable, NAlert } from 'naive-ui';
import type { ApiClient } from '../api/client.ts';
import type { Workspace, User } from '../api/types.ts';
import { useManagement } from '../composables/useManagement.ts';
import { useConfirm } from '../composables/useConfirm.ts';
const props = defineProps<{client: ApiClient; user: User}>();
const emit = defineEmits<{failure: [error: unknown]; refresh: []}>();
const {busy, message, run} = useManagement(props.client, error => emit('failure', error), load); const confirm = useConfirm();
const workspace = ref<Workspace | null>(null); const loginName = ref(''); const role = ref('reader');
const options = [{label: '阅读成员', value: 'reader'}, {label: '编辑成员', value: 'editor'}, {label: '管理员', value: 'admin'}];
async function load() {workspace.value = (await props.client.readMembers()).workspace;}
async function add() {await run(async () => {if (!workspace.value) return; workspace.value = (await props.client.addMember(loginName.value, role.value, workspace.value.version)).workspace; loginName.value = ''; message.value = '成员已添加。'; emit('refresh');});}
async function change(user: User, next: string) {
  if (!workspace.value || !await confirm('修改成员角色', `将“${user.display_name}”的角色改为${options.find(o => o.value === next)?.label}？`)) return;
  await run(async () => {workspace.value = (await props.client.setMemberRole(user.id, next, workspace.value!.version)).workspace; emit('refresh');});
}
async function remove(user: User) {
  if (!workspace.value || !await confirm('移除成员', `确定移除“${user.display_name}”？该账号将失去成员资格，账号本身仍保留。`)) return;
  await run(async () => {workspace.value = (await props.client.removeMember(user.id, workspace.value!.version)).workspace; emit('refresh');});
}
onMounted(() => {void run(load);});
</script>
<template><div class="page-container"><div class="toolbar-row justify-between"><h1 class="section-title">工作区成员</h1><NButton :disabled="busy" @click="run(load)">刷新列表</NButton></div><p class="muted-copy">成员角色决定工作区内的操作权限，与站点管理员身份分开。</p><NAlert v-if="message" type="info" class="mb-5" role="status">{{message}}</NAlert>
  <section class="surface-panel p-5 mb-6"><h2 class="section-title mb-4">添加成员</h2><form class="toolbar-row items-end gap-4" @submit.prevent="add"><NFormItem label="已有账号的登录名" label-for="member-login"><NInput v-model:value="loginName" :disabled="busy" :input-props="{id: 'member-login',required: true}" /></NFormItem><NFormItem label="成员角色" label-for="member-role"><NSelect id="member-role" v-model:value="role" :options="options" class="w-40" :disabled="busy" /></NFormItem><NButton class="mb-6" type="primary" attr-type="submit" :disabled="busy || !workspace" :loading="busy">添加成员</NButton></form></section>
  <div class="overflow-x-auto"><NTable :single-line="false"><thead><tr><th>成员</th><th>角色</th><th>操作</th></tr></thead><tbody><tr v-for="item in workspace?.members" :key="item.user.id"><td>{{item.user.display_name}}<div class="muted-copy">{{item.user.login_name}}</div></td><td><span v-if="item.role === 'owner'">所有者</span><NSelect v-else :value="item.role" :options="options" :disabled="busy" :aria-label="item.user.display_name + '的角色'" class="min-w-36" @update:value="change(item.user, $event)" /></td><td><NButton size="small" :disabled="busy || item.role === 'owner'" @click="remove(item.user)">移除</NButton></td></tr><tr v-if="!workspace?.members.length"><td colspan="3">{{busy ? '正在读取成员…' : '暂无成员'}}</td></tr></tbody></NTable></div>
</div></template>
