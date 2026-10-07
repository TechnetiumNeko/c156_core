<script setup lang="ts">
import { ref } from 'vue';
import { NButton, NInput, NFormItem, NAlert, NDescriptions, NDescriptionsItem } from 'naive-ui';
import type { ApiClient } from '../api/client.ts';
import type { User } from '../api/types.ts';
import { useManagement } from '../composables/useManagement.ts';
import { useConfirm } from '../composables/useConfirm.ts';
const props = defineProps<{client: ApiClient; user: User; workspaceRole: string | null}>();
const emit = defineEmits<{profile: [user: User]; revoked: []; failure: [error: unknown]}>();
const {busy, message, run} = useManagement(props.client, error => emit('failure', error), async () => {emit('profile', (await props.client.session()).user);}); const confirm = useConfirm();
const displayName = ref(props.user.display_name); const oldPassword = ref(''); const password = ref(''); const repeat = ref('');
async function saveProfile() {await run(async () => {const value = await props.client.changeProfile(displayName.value, props.user.version); emit('profile', value.user); message.value = '显示名已更新。';});}
async function savePassword() {
  if (password.value !== repeat.value) {message.value = '两次新密码不一致。'; return;}
  if (!await confirm('修改密码', '修改后所有旧会话将退出。当前未保存的草稿会保留，请重新登录后继续。')) return;
  await run(async () => {try {await props.client.changePassword(oldPassword.value, password.value); emit('revoked');} finally {oldPassword.value = ''; password.value = ''; repeat.value = '';}});
}
const roles: Record<string, string> = {reader: '阅读成员', editor: '编辑成员', admin: '工作区管理员', owner: '工作区所有者'};
</script>
<template><div class="page-container"><h1 class="section-title">个人账号</h1><p class="muted-copy">管理显示名和登录密码。</p>
  <NAlert v-if="message" type="info" class="mb-5" role="status">{{message}}</NAlert>
  <NDescriptions label-placement="top" bordered :column="2" class="mb-6"><NDescriptionsItem label="登录名">{{user.login_name}}</NDescriptionsItem><NDescriptionsItem label="工作区身份">{{roles[workspaceRole ?? ''] ?? '未加入工作区'}}</NDescriptionsItem><NDescriptionsItem label="站点身份">{{user.site_admin ? '站点管理员' : '普通账号'}}</NDescriptionsItem></NDescriptions>
  <div class="grid gap-6 md:grid-cols-2">
    <section class="surface-panel p-6"><h2 class="section-title mb-5">显示名</h2><form @submit.prevent="saveProfile"><NFormItem label="显示名" label-for="profile-name"><NInput v-model:value="displayName" :disabled="busy" :input-props="{id: 'profile-name',required: true}" /></NFormItem><NButton type="primary" attr-type="submit" :loading="busy" :disabled="busy">保存显示名</NButton></form></section>
    <section class="surface-panel p-6"><h2 class="section-title mb-5">修改密码</h2><form @submit.prevent="savePassword"><NFormItem label="原密码" label-for="old-password"><NInput v-model:value="oldPassword" type="password" :disabled="busy" :input-props="{autocomplete: 'current-password',id: 'old-password',required: true}" /></NFormItem><NFormItem label="新密码（15–128 个字符）" label-for="new-password"><NInput v-model:value="password" type="password" :disabled="busy" :input-props="{autocomplete: 'new-password',id: 'new-password',required: true, minlength: 15, maxlength: 128}" /></NFormItem><NFormItem label="确认新密码" label-for="repeat-password"><NInput v-model:value="repeat" type="password" :disabled="busy" :input-props="{autocomplete: 'new-password',id: 'repeat-password',required: true}" /></NFormItem><NButton attr-type="submit" :loading="busy" :disabled="busy">修改密码</NButton></form></section>
  </div>
</div></template>
