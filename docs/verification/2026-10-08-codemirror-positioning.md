# CodeMirror 实时预览定位修复

## 复现与原因

在真实 Chromium 中打开 Vite，将实际 livePreview 扩展与项目样式用于包含三级标题、普通正文和空行的 10 行文档。只读取每行文字位置的 `coordsAtPos`，再用同一坐标调用 `posAtCoords`；没有自动点击、拖选或键盘输入。

修复前，10 行里 8 行映射到其他行。例如第二行正文实际位于 y=117～136，但命中结果落到第三行；之后的标题间距进一步累积错位。

项目 `.cm-md-heading` 对 CodeMirror 行设置了上下 margin。垂直 margin 不能准确计入 CodeMirror 的行高模型。[CodeMirror 维护者对此限制的说明](https://discuss.codemirror.net/t/css-causing-inability-to-click-on-a-line/8919)。

## 改动与结果

只修改 `frontend/src/styles.css`：标题行移除上下 margin，使用 padding-top/padding-bottom，并让规则优先于通用 cm-line padding。保留标题字号和层级。

同一真实浏览器坐标检查修复后，10 行全部命中正确行，采样文字位置也一致。临时复现脚本 `/tmp/c156-cm-geometry.mjs`，截图 `/tmp/c156-cm-geometry.png`。

额外用真实 Markdown/default 换行命令在 EditorState 上检查标题、普通正文、列表、任务项：前两者正常插入换行，后两者正常延续列表标记。没有更换编辑器、修改正文同步或回车键绑定。

`npm --prefix frontend test`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`、`git diff --check` 均通过。原有约 502 kB 编辑器异步 chunk 体积提示仍在，构建成功。

未运行浏览器自动鼠标点选、拖动、回车输入或中文 IME 交互；修复证据是实际 DOM 几何映射与生产换行命令，完整编辑手感仍待人工试用。未新增样式测试或浏览器测试框架。
