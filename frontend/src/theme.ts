import type { GlobalThemeOverrides } from 'naive-ui';
export const tokens = {ink: '#25352e', muted: '#68756e', surface: '#ffffff', canvas: '#f5f6f3', accent: '#287558', line: '#e2e7df'};
export const themeOverrides: GlobalThemeOverrides = {common: {
  primaryColor: tokens.accent, primaryColorHover: '#37876a', primaryColorPressed: '#205f48', primaryColorSuppl: tokens.accent,
  textColorBase: tokens.ink, textColor1: tokens.ink, textColor2: '#4c5d53', textColor3: tokens.muted,
  borderColor: tokens.line, bodyColor: tokens.canvas, cardColor: tokens.surface,
  fontFamily: '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif', borderRadius: '6px',
}, Message: {
  borderRadius: '12px', padding: '13px 16px', fontSize: '14px', lineHeight: '1.6',
  maxWidth: 'min(520px, calc(100vw - 32px))',
  colorSuccess: '#f3faf5', colorWarning: '#fffaf0', colorError: '#fff5f2',
  textColorSuccess: tokens.ink, textColorWarning: tokens.ink, textColorError: tokens.ink,
  iconColorSuccess: tokens.accent, iconColorWarning: '#a37528', iconColorError: '#b34f3d',
}};
export const themeVariables = Object.fromEntries(Object.entries(tokens).map(([name, value]) => ['--' + name, value]));
