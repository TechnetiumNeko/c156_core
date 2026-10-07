<script setup lang="ts">
import { ref, watch, onMounted } from 'vue';
import { renderMarkdown } from '../markdown/render.ts';
const props = defineProps<{source: string}>(); const host = ref<HTMLElement>();
function render() {if (host.value) renderMarkdown(props.source, host.value);}
onMounted(render); watch(() => props.source, render);
</script>
<template><div ref="host" class="markdown-body" /></template>
