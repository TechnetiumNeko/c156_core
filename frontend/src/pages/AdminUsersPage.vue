<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { NButton, NInput, NFormItem, NTable, NTag, NAlert } from 'naive-ui';
import type { ApiClient } from '../api/client.ts';
import type { User, UserAction, AccountGrant } from '../api/types.ts';
import { useManagement } from '../composables/useManagement.ts';
import { useConfirm } from '../composables/useConfirm.ts';
const props = defineProps<{client: ApiClient; user: User}>();
const emit = defineEmits<{profile: [user: User]; failure: [error: unknown]}>();
const {busy, message, run} = useManagement(props.client, error => emit('failure', error), load); const confirm = useConfirm();
const users = ref<User[]>([]); const grant = ref<AccountGrant | null>(null); const loginName = ref(''); const displayName = ref('');
const labels = {activation: '重发激活', reset: '发起密码重置', disable: '停用账号', enable: '启用账号'};
async function load() {users.value = (await props.client.listUsers()).users;}
async function create() {await run(async () => {grant.value = await props.client.createUser(loginName.value, displayName.value); loginName.value = ''; displayName.value = ''; message.value = '账号已创建。请转交激活凭据，并在成员管理中添加工作区成员。'; await load();});}
async function act(action: UserAction, user: User) {
  if (!await confirm(labels[action], `确定对“${user.display_name}”执行${labels[action]}？重置和停用会撤销该账号的会话。`)) return;
  await run(async () => {const value = await props.client.userAction(action, user.id, user.version); if ('token' in value) grant.value = value; else grant.value = null; await load(); message.value = `${labels[action]}成功。`;});
}
async function toggleAdmin(user: User) {
  if (!await confirm('修改站点管理员', `${user.site_admin ? '撤销' : '授予'}“${user.display_name}”的站点管理权限？工作区身份单独管理。`)) return;
  await run(async () => {const value = await props.client.setSiteAdmin(user.id, !user.site_admin, user.version); if (value.user.id === props.user.id) emit('profile', value.user); if (value.user.id !== props.user.id || value.user.site_admin) await load();});
}
async function copy() {await run(async () => {if (grant.value) {await navigator.clipboard.writeText(grant.value.token); message.value = '凭据已复制，请私下转交。';}});}
onMounted(() => {void run(load);});
</script>
<template><div class="page-container"><div class="toolbar-row justify-between"><h1 class="section-title">站点账号</h1><NButton :disabled="busy" @click="run(load)">刷新列表</NButton></div><p class="muted-copy">创建和管理登录账号。工作区成员资格在成员管理中设置。</p>
  <NAlert v-if="message" type="info" role="status" class="mb-5">{{message}}</NAlert>
  <section v-if="grant" class="surface-panel p-5 mb-6"><h2 class="section-title">{{grant.purpose === 'activate' ? '激活凭据' : '密码重置凭据'}}</h2><p class="muted-copy">{{grant.user.login_name}}，有效至 {{new Date(grant.expires_at).toLocaleString()}}。凭据仅在当前页面显示。</p><pre class="credential">{{grant.token}}</pre><div class="toolbar-row"><NButton :disabled="busy" @click="copy">复制凭据</NButton><NButton @click="grant = null">清除凭据</NButton></div></section>
  <section class="surface-panel p-5 mb-6"><h2 class="section-title mb-4">创建账号</h2><form class="flex gap-4 items-end flex-wrap" @submit.prevent="create"><NFormItem label="登录名" label-for="create-login"><NInput v-model:value="loginName" :disabled="busy" :input-props="{id: 'create-login',required: true}" /></NFormItem><NFormItem label="显示名" label-for="create-display"><NInput v-model:value="displayName" :disabled="busy" :input-props="{id: 'create-display',required: true}" /></NFormItem><NButton class="mb-6" type="primary" attr-type="submit" :loading="busy" :disabled="busy">创建账号</NButton></form></section>
  <div class="overflow-x-auto"><NTable :single-line="false"><thead><tr><th>账号</th><th>状态</th><th>站点身份</th><th>操作</th></tr></thead><tbody><tr v-for="item in users" :key="item.id"><td><div class="font-medium">{{item.display_name}}</div><div class="muted-copy">{{item.login_name}}</div></td><td><NTag size="small" :type="item.status === 'active' ? 'success' : 'default'">{{({active:'正常', invited:'待激活', disabled:'已停用'} as Record<string,string>)[item.status] ?? item.status}}</NTag></td><td>{{item.site_admin ? '管理员' : '普通账号'}}</td><td><div class="toolbar-row"><NButton v-if="item.status !== 'active'" size="small" :disabled="busy" @click="act('activation', item)">重发激活</NButton><NButton size="small" :disabled="busy" @click="act('reset', item)">重置密码</NButton><NButton size="small" :disabled="busy" @click="act(item.status === 'disabled' ? 'enable' : 'disable', item)">{{item.status === 'disabled' ? '启用' : '停用'}}</NButton><NButton size="small" :disabled="busy" @click="toggleAdmin(item)">{{item.site_admin ? '撤销管理' : '设为管理员'}}</NButton></div></td></tr><tr v-if="!users.length"><td colspan="4">{{busy ? '正在读取账号…' : '暂无账号'}}</td></tr></tbody></NTable></div>
</div></template>
