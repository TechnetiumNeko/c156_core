# Vue 工作台原型验证

实现分支：`feat/vue-workbench`。采用 Naive UI + UnoCSS，CodeMirror 默认实时预览编辑；新增个人账号、站点账号、工作区成员页面和目录文件菜单，FastAPI 直接调用已有应用服务。

## 实际检查

| 命令或检查 | 结果 |
| --- | --- |
| `npm --prefix frontend test` | 通过；保留已有状态／客户端用例，新增一个真实 CodeMirror 状态装饰用例 |
| `npm --prefix frontend run typecheck` | 通过 |
| `npm --prefix frontend run build` | 通过；管理页、阅读渲染、编辑器按需加载 |
| `python -m unittest tests.test_server_management tests.test_server_api tests.test_server_transport tests.test_server_production tests.test_architecture -q` | 26 项相关检查通过；四个新增 FastAPI 用例使用实际 SQLite 与服务，不重复已有服务测试 |
| `git diff --check` | 通过 |
| 独立临时库、实际 Vite 5173 + FastAPI 8001 | Vue、CodeMirror、UnoCSS 模块均成功响应；经 Vite 的 healthz 返回 ok，bootstrap 返回 initialized=true 和 nonce |
| 新 Vue renderMarkdown 的实际 Marked + DOMPurify 检查 | 中文标题、强调、代码块正常；脚本、SVG 事件、危险链接被清除，图片显示 alt 占位 |
| 真实 Chromium 只读截图 | 登录页面、实际 DocumentEditor 组件在桌面／390px 窄屏正常挂载，无 pageerror，无横向溢出 |

截图位于本机 `/tmp/c156-vue-login-desktop.png`、`/tmp/c156-vue-login-mobile.png`、`/tmp/c156-vue-editor-desktop.png`、`/tmp/c156-vue-editor-mobile.png`。编辑器截图使用临时组件展示数据，不能作为登录、选择文档或保存流程的验收证据。浏览器未自动点击、输入、右键或执行账号操作。

构建仍报告编辑器异步 chunk 约 502 kB（gzip 约 175 kB）的体积提示，构建成功；首屏主 chunk 约 376 kB（gzip 约 112 kB）。早期原型不进一步拆分编辑器依赖。

## 本次发现及修正

- 真实浏览器截图发现原 ApiClient 将 fetch 作为实例方法调用会触发 Illegal invocation；改为局部函数调用，实际登录页会话初始化正常，Node 测试未能暴露这一浏览器绑定问题。
- 独立只读审阅发现 Escape 取消确认时 Promise 不结束，会卡住文件操作 busy；已验证 Naive UI 的 onEsc 路径并补齐取消回调。
- 目录读取重试的错误转交 App，确保 401 进入会话恢复；输入框原生 id/autocomplete 放入 NInput inputProps；凭据 purpose 与账号 status 标签遵循已有服务枚举。

## 范围和未验证项

- 保留原 Markdown、手动保存、修订冲突、身份 epoch 隔离、草稿保留及丢弃确认。账号管理与成员管理分别由服务授权；客户端操作目标不授予当前身份。
- 浏览器登录后编辑保存、中文 IME、文本选择／撤销、文件菜单与管理表单的完整交互手感尚待人工试用。本次只截图，未运行自动交互流程。
- Markdown 表格在阅读模式渲染，实时编辑保留源码；图片采用安全占位。未加入 LaTeX、图片上传、可视化表格编辑、高级对象 ACL 或所有权转移页面。
- 未推送、合并或触发测试站部署；`c156.secret-sealing.club` 仍运行其独立自动部署版本。
- 未修改已有数据库或用户未提交的身份访问设计文档。验证采用独立临时库，临时服务在收尾时停止。
