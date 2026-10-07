<script setup lang="ts">
import { ref, watch } from 'vue';
import { NInput, NSelect, NFormItem, NButton, NAlert } from 'naive-ui';
import type { AccountGrant } from '../api/types.ts';
const props = defineProps<{busy: boolean; options: {label: string; value: string}[]; grant: AccountGrant | null}>();
const emit = defineEmits<{invite: [loginName: string, displayName: string, role: string]; clear: []}>();
const loginName = ref(''); const displayName = ref(''); const role = ref('reader'); const copyMessage = ref('');
watch(() => props.grant, grant => {copyMessage.value = ''; if (grant) {loginName.value = ''; displayName.value = '';}});
async function copy() {if (!props.grant) return; try {await navigator.clipboard.writeText(props.grant.token); copyMessage.value = '凭据已复制，请私下转交。';} catch {copyMessage.value = '无法自动复制，请手动选择下方凭据复制。';}}
</script>
<template><section class="surface-panel p-5 mb-6"><h2 class="section-title mb-4">邀请新账号加入工作区</h2><p class="muted-copy">选择角色并发出激活凭据。用户设置密码后即可使用预分配角色，无需再次添加成员。</p>
  <form class="toolbar-row items-end gap-4" @submit.prevent="emit('invite', loginName, displayName, role)"><NFormItem label="登录名" label-for="invite-login"><NInput v-model:value="loginName" :disabled="busy" :input-props="{id: 'invite-login', required: true, autocomplete: 'off'}" /></NFormItem><NFormItem label="显示名" label-for="invite-display"><NInput v-model:value="displayName" :disabled="busy" :input-props="{id: 'invite-display', required: true}" /></NFormItem><NFormItem label="激活后的角色" label-for="invite-role"><NSelect id="invite-role" v-model:value="role" :options="options" class="w-40" :disabled="busy" /></NFormItem><NButton class="mb-6" type="primary" attr-type="submit" :disabled="busy" :loading="busy">创建账号并邀请</NButton></form>
  <div v-if="grant" class="border-t border-line pt-4"><h3 class="m-0 text-base font-medium">{{grant.user.display_name}}的激活凭据</h3><p class="muted-copy">有效至 {{new Date(grant.expires_at).toLocaleString()}}。仅在当前页面显示，请复制后转交；遗失可在站点账号页重发。</p><pre class="credential">{{grant.token}}</pre><div class="toolbar-row"><NButton :disabled="busy" @click="copy">复制凭据</NButton><NButton :disabled="busy" @click="emit('clear')">清除凭据</NButton></div><NAlert v-if="copyMessage" type="info" role="status" class="mt-3">{{copyMessage}}</NAlert></div>
</section></template>
