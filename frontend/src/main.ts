import { createApp, h } from 'vue';
import { NConfigProvider, NDialogProvider, zhCN, dateZhCN } from 'naive-ui';
import App from './App.vue';
import { themeOverrides, themeVariables } from './theme.ts';
import 'virtual:uno.css';
import './styles.css';
createApp({render: () => h(NConfigProvider, {themeOverrides, style: themeVariables, locale: zhCN, dateLocale: dateZhCN}, {default: () => h(NDialogProvider, null, {default: () => h(App)})})}).mount('#app');
