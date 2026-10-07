<script setup lang="ts">
import { ref } from 'vue';
import { NButton, NInput, NFormItem, NRadioGroup, NRadioButton, NAlert } from 'naive-ui';
import type { ApiClient } from '../api/client.ts';
const props = defineProps<{busy: boolean; ready: boolean; client: ApiClient}>();
const emit = defineEmits<{login: [name: string, password: string]; failure: [error: unknown]}>();
const name = ref(''); const password = ref(''); const token = ref(''); const confirmPassword = ref('');
const mode = ref<'login' | 'activate' | 'reset'>('login'); const localBusy = ref(false); const message = ref('');
function changeMode() {password.value = ''; token.value = ''; confirmPassword.value = ''; message.value = '';}
async function submit() {
  if (props.busy || localBusy.value || !props.ready) return;
  if (mode.value === 'login') {const secret = password.value; password.value = ''; emit('login', name.value, secret); return;}
  if (password.value !== confirmPassword.value) {message.value = '两次密码不一致，请重新输入。'; return;}
  localBusy.value = true; message.value = '';
  try {
    if (mode.value === 'activate') await props.client.activate(token.value, password.value);
    else await props.client.resetPassword(token.value, password.value);
    mode.value = 'login'; message.value = '密码已设置，请使用账号登录。';
  } catch (error) {emit('failure', error);}
  finally {password.value = ''; token.value = ''; confirmPassword.value = ''; localBusy.value = false;}
}
</script>
<template>
  <div class="login-panel">
    <div class="mb-6"><h2 class="m-0 text-2xl font-semibold">{{mode === 'login' ? '登录工作台' : mode === 'activate' ? '激活账号' : '重置密码'}}</h2><p class="muted-copy mt-2">{{mode === 'login' ? '登录后浏览目录，继续编辑你的文档。' : '使用管理员提供的一次性凭据设置密码。'}}</p></div>
    <NRadioGroup v-model:value="mode" size="small" @update:value="changeMode"><NRadioButton value="login">登录</NRadioButton><NRadioButton value="activate">激活账号</NRadioButton><NRadioButton value="reset">重置密码</NRadioButton></NRadioGroup>
    <NAlert v-if="message" class="mt-4" type="info" role="status">{{message}}</NAlert>
    <form class="mt-5" @submit.prevent="submit">
      <NFormItem v-if="mode === 'login'" label="账号" label-for="login-name"><NInput v-model:value="name" :disabled="busy || localBusy" :input-props="{autocomplete: 'username',id: 'login-name',required: true}" /></NFormItem>
      <NFormItem v-else label="一次性凭据" label-for="account-token"><NInput v-model:value="token" :disabled="localBusy" :input-props="{autocomplete: 'off',id: 'account-token',required: true}" /></NFormItem>
      <NFormItem :label="mode === 'login' ? '密码' : '新密码（15–128 个字符）'" label-for="login-password"><NInput v-model:value="password" type="password" show-password-on="click"  :disabled="busy || localBusy" :input-props="{autocomplete: mode === 'login' ? 'current-password' : 'new-password',id: 'login-password',required: true, ...(mode === 'login' ? {} : {minlength: 15, maxlength: 128})}" /></NFormItem>
      <NFormItem v-if="mode !== 'login'" label="确认密码" label-for="confirm-password"><NInput v-model:value="confirmPassword" type="password" :disabled="localBusy" :input-props="{autocomplete: 'new-password',id: 'confirm-password',required: true}" /></NFormItem>
      <NButton block type="primary" attr-type="submit" :loading="busy || localBusy" :disabled="!ready || busy || localBusy">{{mode === 'login' ? '登录' : '设置密码'}}</NButton>
    </form>
  </div>
</template>
