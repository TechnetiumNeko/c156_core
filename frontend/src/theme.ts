import type { GlobalThemeOverrides } from 'naive-ui';
export const tokens = {ink: '#25352e', muted: '#68756e', surface: '#ffffff', canvas: '#f5f6f3', accent: '#287558', line: '#e2e7df'};
export const themeOverrides: GlobalThemeOverrides = {common: {
  primaryColor: tokens.accent, primaryColorHover: '#37876a', primaryColorPressed: '#205f48', primaryColorSuppl: tokens.accent,
  textColorBase: tokens.ink, textColor1: tokens.ink, textColor2: '#4c5d53', textColor3: tokens.muted,
  borderColor: tokens.line, bodyColor: tokens.canvas, cardColor: tokens.surface,
  fontFamily: '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif', borderRadius: '6px',
}};
export const themeVariables = Object.fromEntries(Object.entries(tokens).map(([name, value]) => ['--' + name, value]));
