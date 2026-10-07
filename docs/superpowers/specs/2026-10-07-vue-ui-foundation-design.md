# Vue 工作台统一界面方案

日期：2026-10-07

## 目标与已确认路线

用户希望把占位式 Vue 页面更新为具有统一样式的文档工作台，并确认 `npm run dev` 开发链路能正常运行。用户已确认 Naive UI 与 UnoCSS。

保留 Vue 3、TypeScript、Vite、FastAPI 和已有 HTTP 契约。用户已批准原统一界面方案，随后确认采用 CodeMirror 实时预览，并要求补齐管理员页面、个人页面、登录与目录右键操作。本次范围扩展为完整基础工作台：统一界面、Markdown 编辑、账号入口及文件操作。下文新增页面范围、HTTP 接口和编辑器边界待用户确认后实施。

## 依赖与样式职责

- Naive UI 提供按钮、输入框、卡片、提示、标签等交互组件，按需显式导入。
- UnoCSS 通过 Vite 插件与 `presetWind3` 生成布局、间距、响应式和外观工具类。使用标准 class 属性，不增加属性模式或自动导入插件。
- CodeMirror 6 提供文本编辑、选区、撤销、语法树和 Markdown 装饰；Marked 与 DOMPurify 提供阅读模式的 Markdown 渲染与净化。全部依赖由 npm 锁定并本地打包，不依赖 CDN。
- `frontend/src/theme.ts` 集中定义颜色、字体、圆角等设计变量，并生成 Naive UI 的主题覆盖。根级 `NConfigProvider` 同时向容器提供对应 CSS 变量，UnoCSS 主题引用同一组 CSS 变量。
- `frontend/uno.config.ts` 维护主题映射和少量具有明确用途的 shortcuts，例如表面容器、工具栏和目录行。
- `frontend/src/styles.css` 只保留基础样式、字体、焦点和编辑正文等必要规则；页面布局使用 UnoCSS。不引入另一套 utility 框架，不使用全局 reset 覆盖 Naive UI 内部样式。

保留 npm 与 package-lock。安装时检查新增依赖与现有 Vue、Node 22、Vite 8 的兼容性，锁定实际使用版本。

## 视觉与页面组织

默认浅色主题，采用偏暖的页面底色、白色内容面板、深灰正文和低饱和绿色强调色。字体使用系统中文无衬线栈，正文编辑使用系统等宽字体栈，不依赖在线字体。

桌面顶栏显示 C156、工作台名称、当前账号和退出操作。登录区域使用宽度受限的卡片，明确账号、密码与提交状态；匿名时仍展示保留的会话失效草稿，以便复制。

登录后采用左侧目录与右侧编辑区。目录区有标题、层级缩进、明确的展开符号和选中行；编辑区包含文档名称、权限或保存状态、保存按钮及快捷键说明。顶栏账号菜单提供个人账号和按权限显示的管理员入口。没有选中文档时显示简洁的选择提示。窄屏上下排列，工具栏允许换行，避免正文和长目录名称撑宽页面。

颜色、字号、间距、边框和圆角均从统一主题获取。文案直接描述状态和可用操作，不添加宣传语、虚构指标或无功能按钮。

## 组件与数据边界

沿用现有组件边界：

| 文件 | 职责 |
| --- | --- |
| `App.vue` | 主题容器、工作台布局、会话和业务操作协调 |
| `LoginPanel.vue` | 登录表单，保持原 props/emits 和密码提交后清空行为 |
| `DirectoryTree.vue` | 递归展示目录与读取状态，保持原目录状态驱动的展开逻辑 |
| `DocumentEditor.vue` | 编辑正文、工具栏和状态，保持原 edit/save/latest/merge 事件 |
| `ConflictPanel.vue` | 冲突说明、最新正文和合并前草稿参考 |
| `MarkdownEditor.vue`、`editor/` | CodeMirror 生命周期、Markdown 语法展示及格式命令 |
| `MarkdownPreview.vue`、`markdown/` | 只读渲染与净化，不修改正文 |
| `pages/` | 个人账号、站点账号管理和工作区成员管理 |
| `composables/` | 文件操作、管理请求的状态与界面协调 |
| `theme.ts`、`uno.config.ts` | 统一主题与样式工具配置 |

保持 `api/`、`state/` 的职责，新增请求集中在 ApiClient，视图通过业务协调层调用。目录继续使用递归组件，不为采用组件库而重建为另一套目录状态。CodeMirror 包装组件接收正文、文档身份及只读状态，输出用户正文变更，不访问 API。草稿和保存基础仍以 EditorState 为唯一业务状态来源。业务忙碌不自动禁止正文输入；会话暂停或权限只读必须同时设置 CodeMirror 的只读与不可编辑状态。

主题不引入持久化或全局状态库。管理页作为现有工作台内的页面切换，不为这次交付新增路由依赖。切换管理页保留编辑器实例及内存草稿；退出、身份变化、删除当前内容仍须遵守草稿保护。

## Markdown 编辑体验

默认是单区域实时预览编辑：标题、粗体、斜体、删除线、列表、任务项、引用、链接、行内代码和代码块获得可读的格式。非活动语法范围隐藏适合隐藏的标记；光标或选区触及相关语法范围时恢复标记，跨段选择不能隐藏选区内源码。格式装饰不改写底层 Markdown，不经过富文本再序列化。

工具栏提供标题、粗体、斜体、列表、引用、链接和代码操作，操作直接产生文本事务并进入 CodeMirror 撤销历史。链接编辑不能触发意外导航。中文组合输入时不替换正文或重建编辑器；外部正文更新按文档身份及内容差异同步，用户编辑回传不反复 setContent。

完整源码和独立阅读模式放入次级展示菜单，默认不要求用户在编辑与预览面板之间切换。只读文档默认渲染阅读，会话失效的未保存草稿仍可选择复制。表格按 Markdown 文本保留并在阅读模式渲染，本阶段不做可视化表格单元格编辑。未知语法保留可编辑源码，不静默丢弃。LaTeX、自定义 Markdown 和图片上传留待明确支持范围后扩展；本阶段不宣称已支持。图片沿用旧阅读策略显示 alt 占位，不自动请求外部媒体。

实时预览扩展使用 CodeMirror 的语法树及 Decoration，不手写 DOM 编辑器，不额外采用未经评估的第三方实时预览框架。处理可见区域和必要的语法上下文，避免每次光标移动全量重建文档。阅读 HTML 必须先经 DOMPurify 净化，并沿用旧预览的危险标签、属性、链接与媒体规则。

## 账号与管理页面

- 登录页：正常登录、一次性凭据激活、密码重置。激活和重置使用管理员人工转交的凭据，不新增开放注册或邮件系统。
- 个人账号页：查看登录名和角色、修改显示名、修改密码、退出。修改密码沿用后端撤销会话语义，保留草稿并明确提示重新登录。
- 站点账号管理页：账号列表、创建账号、重发激活、发起密码重置、启用或停用、授予或撤销站点管理员。一时凭据仅在当前会话展示，可复制和明确清除，退出或身份切换时清除。
- 工作区成员管理页：成员列表、按登录名添加成员、调整角色、移除成员。仅 admin/owner 可用，站点管理员身份不自动授予工作区管理权限。创建账号与加入工作区是两个独立操作，界面分别说明。

以上均复用现有 IdentityService、AccountService、AccessService 的规则和版本检查。高级对象 ACL、工作区所有权转移和公开阅读策略先不扩展进本次页面。

## 文件操作与新增 HTTP 契约

目录行右键菜单提供按权限可用的新建文档、新建文件夹、打开或编辑、重命名、删除；文件夹新建以该文件夹为父目录，文档行新建以其父目录为目标。另有可见的操作按钮提供相同菜单，支持触屏和键盘，不把右键作为唯一入口。目录顶部提供在根目录新建的入口。

新建后刷新受影响的目录缓存，文档按正常选中流程打开；重命名刷新目录和当前文档名称，保留草稿、修订基础和撤销历史。删除先显示确认信息，删除当前文档或包含当前文档的文件夹时先处理草稿；递归删除必须先向服务请求 DeleteSnapshot，再携带其 subtree_token，树变化时拒绝执行并要求重新确认。根及受保护节点不提供不合法操作。

FastAPI 新增分组 routes 模块，直接调用应用服务，不转调旧 API.dispatch，不修改领域、数据库协议或授权规则。原 routes 继续保留已发布的浏览和保存接口。接口方案如下，查询参数和 JSON 字段采用现有命名约定，严格拒绝未知字段。

| 接口 | 请求及结果 |
| --- | --- |
| POST `/api/auth/activate`、`/api/auth/reset` | token、password；返回 user，沿用 nonce 和限流 |
| PUT `/api/account/profile` | display_name、expected_version；返回 user |
| PUT `/api/account/password` | old_password、new_password；成功清除会话 Cookie |
| GET/POST `/api/admin/users` | 列表／login_name、display_name 创建；沿用旧入口响应 |
| POST `/api/admin/users/activation`、`reset`、`disable`、`enable` | user_id、expected_version；沿用旧入口响应 |
| PUT `/api/admin/users/site-admin` | user_id、enabled、expected_version；返回 user |
| GET/POST/PUT/DELETE `/api/workspace/members` | 沿用旧入口字段及 workspace 响应 |
| POST `/api/folder`、`/api/document` | parent_id、name，文档可选 content；返回 node/document 与 access |
| PUT `/api/node/rename` | object_id、name、expected_version；返回 node |
| GET `/api/folder/delete-plan?folder_id=…` | 返回 object_id、version、items（node 与 depth）、subtree_token |
| DELETE `/api/node` | object_id、expected_version、recursive，递归时 expected_subtree_token；返回 ok |

所有会话写操作执行 CSRF 验证，所有授权由应用服务执行。账号 version、目录结构 version、工作区授权 version 与正文 revision_id 各用其原语义，不能互换。新增页面请求同样绑定会话 epoch，过期响应不能写回新用户界面，网络失败不自动重试新增账号、创建或删除等写操作。

## 状态与行为约束

- 加载与读取失败分别展示，失败不伪装成空目录。
- API 错误及草稿保留提示使用持久可见的提示区域，不以短暂 toast 代替。
- 保存中、未保存、保存结果未确认、冲突、权限读取失败、只读和会话暂停保留其现有语义与优先级。
- 保留原按钮禁用条件与权限判断，视图样式不能放宽编辑或保存权限。
- 保留 Ctrl/Cmd+S、关闭页面提醒、身份切换保护、请求 epoch 隔离及原 revision_id 重试行为。
- 表单字段有可关联的标签，状态保留 role=status/alert，目录保留 aria-expanded/aria-current，焦点清晰可见。状态除了颜色也用文字表达。

## 开发链路与验证

2026-10-07 已运行现有 `npm --prefix frontend test`（22 项通过）、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`。

另已实际启动 `npm --prefix frontend run dev` 与 `python run_server.py --database <独立临时库> --port 8001`。页面、Vite 客户端、main.ts、App.vue、styles.css 均返回 200，经 Vite 代理的 `/api/healthz` 返回 ok，`/api/bootstrap` 返回符合现有契约的匿名响应。空库未创建管理员，尚未验证登录后的编辑保存；验证后已停止临时服务。

实施后运行相同三项前端检查，并重启开发服务器验证 Vue 模块、UnoCSS 虚拟模块和 API 代理的实际响应。新增 API 使用项目现有 FastAPI 测试工具与临时数据库验证授权、CSRF、版本冲突、会话撤销和真实数据写入；递归删除覆盖子树变化拒绝执行。CodeMirror 扩展及包装组件只为独立关键行为添加简短测试，例如活动选区显示标记、展示不修改正文、外部同步不覆盖用户继续输入、只读不可编辑。复用现有草稿保护与服务层测试，不另造重复大型框架。

可用浏览器时仅打开并截图查看桌面及窄屏，不自动点击、填表或执行交互流程。界面手感、IME 输入与菜单交互的人工验收明确列为未验证项，不将 HTTP 检查或 mock 测试等同于浏览器验收。

更新工作台开发文档，说明主题和样式的维护位置。本地库与原始数据不修改，测试环境 `c156.secret-sealing.club` 的自动部署不属于本次开发链路检查。

## 完成标准

工作台使用 Naive UI 与 UnoCSS 的统一主题；CodeMirror 默认实时预览且保留原 Markdown；账号页、站点账号管理和工作区成员管理具备上述操作；文件菜单的新建、重命名和删除调用真实服务；已有业务操作和草稿保护保持有效；加载、错误、只读与冲突状态清楚可辨；桌面与窄屏内容不溢出；相关测试、类型检查、构建及实际开发服务响应检查通过。报告实际证据及未验证的浏览器交互。
