import { createApp, h } from 'vue';
import { NConfigProvider, NDialogProvider, NMessageProvider, zhCN, dateZhCN } from 'naive-ui';
import App from './App.vue';
import { themeOverrides, themeVariables } from './theme.ts';
import 'virtual:uno.css';
import './styles.css';
createApp({render: () => h(NConfigProvider, {themeOverrides, style: themeVariables, locale: zhCN, dateLocale: dateZhCN}, {default: () => h(NDialogProvider, null, {default: () => h(NMessageProvider, {placement: 'top', max: 3, closable: true, keepAliveOnHover: true, containerClass: 'operation-notices'}, {default: () => h(App)})})})}).mount('#app');
