<script setup lang="ts">
import { ref } from 'vue';
defineProps<{ busy: boolean; ready: boolean }>();
const emit = defineEmits<{ login: [name: string, password: string] }>();
const name = ref(''); const password = ref('');
function submit() { const secret = password.value; password.value = ''; emit('login', name.value, secret); }
</script>
<template><form @submit.prevent="submit"><label>账号 <input v-model="name" autocomplete="username" required :disabled="busy"></label><label>密码 <input v-model="password" type="password" autocomplete="current-password" required :disabled="busy"></label><button :disabled="busy || !ready">登录</button></form></template>
