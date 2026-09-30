// Markdown 转 HTML 后统一净化；正文不变，图片只显示 alt 说明。
export function renderPreview(source, target, environment = window) {
  const html = environment.marked.parse(source, { async: false });
  const safe = environment.DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: [
      'iframe',
      'object',
      'embed',
      'form',
      'input',
      'button',
      'textarea',
      'select',
      'option',
      'video',
      'audio',
      'source',
      'track',
      'style',
      'link',
      'meta',
      'base',
    ],
    FORBID_ATTR: ['style', 'srcset', 'background', 'ping', 'autofocus'],
    RETURN_DOM_FRAGMENT: true,
  });
  for (const img of safe.querySelectorAll('img')) {
    const label = target.ownerDocument.createElement('span');
    label.className = 'image-description';
    label.textContent = img.getAttribute('alt')
      ? `[图片：${img.getAttribute('alt')}]`
      : '[图片]';
    img.replaceWith(label);
  }
  for (const link of safe.querySelectorAll('a')) {
    const href = link.getAttribute('href') || '';
    if (!/^(https?:|mailto:|#)/i.test(href)) link.removeAttribute('href');
    link.setAttribute('rel', 'noopener noreferrer');
  }
  target.replaceChildren(safe);
}
