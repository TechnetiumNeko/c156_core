import { marked } from 'marked';
import createDOMPurify from 'dompurify';
export function renderMarkdown(source: string, target: HTMLElement) {
  const environment = target.ownerDocument.defaultView;
  if (!environment) return;
  const purifier = createDOMPurify(environment);
  const fragment = purifier.sanitize(marked.parse(source, {async: false, gfm: true}), {
    USE_PROFILES: {html: true}, FORBID_TAGS: ['iframe', 'object', 'embed', 'form', 'input', 'button', 'textarea', 'select', 'option', 'video', 'audio', 'source', 'track', 'style', 'link', 'meta', 'base'],
    FORBID_ATTR: ['style', 'srcset', 'background', 'ping', 'autofocus'], RETURN_DOM_FRAGMENT: true,
  });
  for (const img of Array.from(fragment.querySelectorAll('img'))) {
    const label = target.ownerDocument.createElement('span'); label.className = 'image-description'; label.textContent = img.getAttribute('alt') ? `[图片：${img.getAttribute('alt')}]` : '[图片]'; img.replaceWith(label);
  }
  for (const link of Array.from(fragment.querySelectorAll('a'))) {
    if (!/^(https?:|mailto:|#)/i.test(link.getAttribute('href') || '')) link.removeAttribute('href');
    link.setAttribute('rel', 'noopener noreferrer');
  }
  target.replaceChildren(fragment);
}
