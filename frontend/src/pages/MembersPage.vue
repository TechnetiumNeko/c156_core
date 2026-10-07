<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { NButton, NInput, NSelect, NFormItem, NTable, NAlert } from 'naive-ui';
import type { ApiClient } from '../api/client.ts';
import WorkspaceInvitePanel from '../components/WorkspaceInvitePanel.vue';
import type { Workspace, User, AccountGrant } from '../api/types.ts';
import { useManagement } from '../composables/useManagement.ts';
import { useConfirm } from '../composables/useConfirm.ts';
const props = defineProps<{client: ApiClient; user: User; workspaceRole: string | null}>();
const emit = defineEmits<{failure: [error: unknown]; refresh: []}>();
const {busy, message, run} = useManagement(props.client, error => emit('failure', error), load); const confirm = useConfirm();
const workspace = ref<Workspace | null>(null); const loginName = ref(''); const role = ref('reader');
const isOwner = computed(() => props.workspaceRole === 'owner');
const options = computed(() => [{label: '阅读成员', value: 'reader'}, {label: '编辑成员', value: 'editor'}, ...(isOwner.value ? [{label: '管理员', value: 'admin'}] : [])]);
const readScopes = [{label: '仅工作区成员', value: 'members'}, {label: '所有已登录用户', value: 'authenticated'}, {label: '所有人', value: 'everyone'}];
const invitation = ref<AccountGrant | null>(null);
async function invite(loginName: string, displayName: string, role: string) {
  await run(async () => {if (!workspace.value) return; const result = await props.client.inviteMember(loginName, displayName, role, workspace.value.version); workspace.value = result.workspace; invitation.value = result; message.value = '账号和工作区角色已配置，请转交激活凭据。';});
}
const readScope = ref('members'); const ownershipTarget = ref<string | null>(null);
const ownerTargets = computed(() => (workspace.value?.members ?? []).filter(item => item.status === 'active' && item.user.status === 'active' && item.user.id !== props.user.id).map(item => ({label: item.user.display_name + ' (' + item.user.login_name + ')', value: item.user.id})));
async function saveReadScope() {
  if (!workspace.value || readScope.value === workspace.value.read_scope) return;
  if (!await confirm('修改默认阅读范围', '改为“' + readScopes.find(item => item.value === readScope.value)?.label + '”。私密范围和内容上的单独权限规则仍然生效，是否继续？')) return;
  await run(async () => {workspace.value = (await props.client.setReadScope(readScope.value, workspace.value!.version)).workspace; emit('refresh');});
}
async function transfer() {
  if (!workspace.value || !ownershipTarget.value) return;
  const target = ownerTargets.value.find(item => item.value === ownershipTarget.value);
  if (!await confirm('转交工作区所有权', '将所有权转交给“' + target?.label + '”。你的角色将变为管理员，无法再任免管理员或转交所有权，是否继续？')) return;
  await run(async () => {workspace.value = (await props.client.transferOwnership(ownershipTarget.value!, workspace.value!.version)).workspace; emit('refresh');});
}
async function load() {workspace.value = (await props.client.readMembers()).workspace; readScope.value = workspace.value.read_scope;}
async function add() {await run(async () => {if (!workspace.value) return; workspace.value = (await props.client.addMember(loginName.value, role.value, workspace.value.version)).workspace; loginName.value = ''; message.value = '成员已添加。'; emit('refresh');});}
async function change(user: User, next: string) {
  if (!workspace.value || !await confirm('修改成员角色', `将“${user.display_name}”的角色改为${options.value.find(o => o.value === next)?.label}？`)) return;
  await run(async () => {workspace.value = (await props.client.setMemberRole(user.id, next, workspace.value!.version)).workspace; emit('refresh');});
}
async function remove(user: User) {
  if (!workspace.value || !await confirm('移除成员', `确定移除“${user.display_name}”？该账号将失去成员资格，账号本身仍保留。`)) return;
  await run(async () => {workspace.value = (await props.client.removeMember(user.id, workspace.value!.version)).workspace; emit('refresh');});
}
onMounted(() => {void run(load);});
</script>
<template><div class="page-container"><div class="toolbar-row justify-between"><h1 class="section-title">默认工作区管理</h1><NButton :disabled="busy" @click="run(load)">刷新设置</NButton></div><p class="muted-copy">设置默认阅读范围并管理成员。工作区管理权限与站点管理员身份分开。</p><NAlert v-if="message" type="info" class="mb-5" role="status">{{message}}</NAlert>
  <section class="surface-panel p-5 mb-6"><h2 class="section-title mb-4">默认阅读范围</h2><p class="muted-copy">为未单独配置权限的内容设置阅读基线。私密内容和单独配置的权限仍然生效；阅读范围不授予编辑权。</p><form class="toolbar-row items-end gap-4" @submit.prevent="saveReadScope"><NFormItem label="谁可以阅读" label-for="workspace-read-scope"><NSelect id="workspace-read-scope" v-model:value="readScope" :options="readScopes" class="w-56" :disabled="busy || !workspace" /></NFormItem><NButton class="mb-6" type="primary" attr-type="submit" :disabled="busy || !workspace || readScope === workspace.read_scope" :loading="busy">保存阅读范围</NButton></form></section>
  <section v-if="isOwner" class="surface-panel p-5 mb-6"><h2 class="section-title mb-4">转交所有权</h2><p class="muted-copy">仅可转交给已激活的现有成员。转交后你仍是管理员。</p><form class="toolbar-row items-end gap-4" @submit.prevent="transfer"><NFormItem label="新的所有者" label-for="ownership-target"><NSelect id="ownership-target" v-model:value="ownershipTarget" :options="ownerTargets" placeholder="选择成员" class="w-64" :disabled="busy || !workspace" /></NFormItem><NButton class="mb-6" attr-type="submit" :disabled="busy || !ownershipTarget" :loading="busy">转交所有权</NButton></form></section>
  <WorkspaceInvitePanel v-if="user.site_admin" :busy="busy || !workspace" :options="options" :grant="invitation" @invite="invite" @clear="invitation = null" />
  <section class="surface-panel p-5 mb-6"><h2 class="section-title mb-4">添加已有账号</h2><form class="toolbar-row items-end gap-4" @submit.prevent="add"><NFormItem label="已有账号的登录名" label-for="member-login"><NInput v-model:value="loginName" :disabled="busy" :input-props="{id: 'member-login',required: true}" /></NFormItem><NFormItem label="成员角色" label-for="member-role"><NSelect id="member-role" v-model:value="role" :options="options" class="w-40" :disabled="busy" /></NFormItem><NButton class="mb-6" type="primary" attr-type="submit" :disabled="busy || !workspace" :loading="busy">添加成员</NButton></form></section>
  <div class="overflow-x-auto"><NTable :single-line="false"><thead><tr><th>成员</th><th>角色</th><th>操作</th></tr></thead><tbody><tr v-for="item in workspace?.members" :key="item.user.id"><td>{{item.user.display_name}}<div class="muted-copy">{{item.user.login_name}}<span v-if="item.status === 'removed'">，已移除</span><span v-else-if="item.user.status === 'invited'">，待激活</span><span v-else-if="item.user.status === 'disabled'">，账号已停用</span><span v-else-if="item.user.status === 'reset_required'">，待重置密码</span></div></td><td><span v-if="item.role === 'owner'">所有者</span><span v-else-if="item.role === 'admin' && !isOwner">管理员</span><NSelect v-else :value="item.role" :options="options" :disabled="busy || item.status !== 'active'" :aria-label="item.user.display_name + '的角色'" class="min-w-36" @update:value="change(item.user, $event)" /></td><td><NButton size="small" :disabled="busy || item.status !== 'active' || item.role === 'owner' || (item.role === 'admin' && !isOwner)" @click="remove(item.user)">移除</NButton></td></tr><tr v-if="!workspace?.members.length"><td colspan="3">{{busy ? '正在读取成员…' : '暂无成员'}}</td></tr></tbody></NTable></div>
</div></template>
