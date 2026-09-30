# 本地浏览器依赖

工作台运行时只从本站静态白名单加载这些文件，不访问 CDN。

| 文件 | 包和版本 | 来源 | 许可证 |
| --- | --- | --- | --- |
| `marked.umd.js` | Marked 18.0.14 | [npm 发布包](https://registry.npmjs.org/marked/-/marked-18.0.14.tgz)，`package/lib/marked.umd.js` | MIT，见 `marked-LICENSE` |
| `purify.min.js` | DOMPurify 3.4.16 | [npm 发布包](https://registry.npmjs.org/dompurify/-/dompurify-3.4.16.tgz)，`package/dist/purify.min.js` | Apache-2.0 OR MPL-2.0，见 `dompurify-LICENSE` 与 `dompurify-LICENSE-MPL` |

下载时验证 npm registry 提供的 tarball integrity；各文件的 SHA-256、发布包 URL、归档路径及版本记录在 `manifest.json`。依赖升级应同步资产、许可证、manifest 和预览验证。
