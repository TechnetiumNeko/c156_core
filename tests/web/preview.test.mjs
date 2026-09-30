import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { renderPreview } from '../../src/web/static/preview.js';
let JSDOM;
const require = createRequire(import.meta.url);
try {
  ({ JSDOM } = require(process.env.C156_JSDOM_PATH || 'jsdom'));
} catch (error) {
  // 未安装可选依赖时跳过；明确配置的错误路径应直接报错。
  if (process.env.C156_JSDOM_PATH || error.code !== 'MODULE_NOT_FOUND') throw error;
}
test(
  'real vendor sanitizer removes executable markup and external media',
  {
    skip: !JSDOM
      ? 'Set C156_JSDOM_PATH to an external jsdom installation to run vendor sanitizer tests'
      : false,
  },
  () => {
    const dom = new JSDOM('<article></article>', { runScripts: 'outside-only' });
    const w = dom.window;
    for (const filename of ['marked.umd.js', 'purify.min.js'])
      w.eval(
        fs.readFileSync(
          new URL('../../src/web/static/vendor/' + filename, import.meta.url),
          'utf8',
        ),
      );
    const target = w.document.querySelector('article');
    renderPreview(
      '# 标题\n\n<img src="https://example.org/pixel" alt="说明" onerror="alert(1)"><script>alert(1)</script><iframe src="https://evil.example"></iframe><form><input></form><video src="https://evil.example/v"></video><svg onload="alert(1)"></svg>\n\n[坏链接](javascript:alert(1))\n\n[正常链接](https://example.org)\n\n```html\n<script>plain text</script>\n```',
      target,
      w,
    );
    assert.equal(target.querySelector('h1').textContent, '标题');
    assert.equal(target.querySelector('img,script,iframe,form,input,video,svg'), null);
    assert.ok(target.textContent.includes('[图片：说明]'));
    assert.ok(
      target.querySelector('pre').textContent.includes('<script>plain text</script>'),
    );
    assert.equal(target.querySelector('[onerror],[onload]'), null);
    assert.ok(
      [...target.querySelectorAll('a')].every(
        (a) => !a.getAttribute('href')?.startsWith('javascript:'),
      ),
    );
    assert.ok(target.querySelector('a[href="https://example.org"]'));
    dom.window.close();
  },
);
