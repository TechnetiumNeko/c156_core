import { defineConfig, presetWind3 } from 'unocss';
export default defineConfig({presets: [presetWind3()], theme: {colors: {ink: 'var(--ink)', muted: 'var(--muted)', surface: 'var(--surface)', canvas: 'var(--canvas)', accent: 'var(--accent)', line: 'var(--line)'}}, shortcuts: {
  'surface-panel': 'bg-surface border border-line rounded-lg',
  'section-title': 'm-0 text-lg font-semibold text-ink',
  'muted-copy': 'text-sm text-muted leading-relaxed',
  'page-container': 'max-w-5xl mx-auto w-full p-4 md:p-8',
  'toolbar-row': 'flex items-center gap-2 flex-wrap',
}});
