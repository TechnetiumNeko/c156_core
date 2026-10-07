# Vue 完整基础工作台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution, or superpowers:subagent-driven-development if the user selects delegation. Track tasks with the checkboxes below.

**Goal:** 交付 Naive UI + UnoCSS 工作台、CodeMirror Markdown 实时预览、账号页面和文件操作，并验证本地 dev 链路。

**Architecture:** FastAPI 只适配现有 IdentityService、AccountService、AccessService、ContentService。ApiClient 统一管理请求证明及 epoch；业务协调层处理页面操作；CodeMirror 只负责文本与展示，EditorState 继续持有草稿和修订基础。

**Tech Stack:** Vue 3、TypeScript、Vite 8、Naive UI、UnoCSS presetWind3、CodeMirror 6、Marked、DOMPurify、FastAPI、SQLite；Node 22。

**Spec:** [已确认的完整方案](../specs/2026-10-07-vue-ui-foundation-design.md)

## 全局约束

- 所有身份来自会话 Cookie；客户端 user_id 只能作为管理目标，不能作为当前身份。
- 所有会话写操作执行 CSRF 验证；激活／重置执行 nonce 验证和已有限流。
- 站点管理权与工作区管理权分别由应用服务检查，不从 UI 显示推导授权。
- 不修改数据库协议、服务规则或旧入口，不添加自动保存、注册或邮件系统。
- Markdown 源文为正文真值，展示不得重写正文；现有 revision_id、epoch 与草稿保护语义不变。
- 不自动浏览器点击、填表或测试交互流程；浏览器只打开与截图。
- 运行相关已有检查。新增测试只覆盖本次新增关键行为，不给纯样式添加测试。
- 不改动用户已修改的 `2026-10-02-identity-access-design.md`；不推送或触发部署。

## 审阅重点

1. 管理目标 ID 和伪造角色字段不能替代当前身份；Task 1 覆盖越权及未知字段拒绝。
2. 改密、账号停用、管理员身份变化后旧证明和旧页面数据不得继续授权；Task 1/2 覆盖撤销与迟到响应。
3. 选区跨 Markdown 范围、中文组合输入和保存期间继续输入不得丢字；Task 3 覆盖源码不变、外部同步与只读，IME 手感留人工确认。
4. 删除目录过程中子树变化、草稿所在文档被包含在删除目标中必须拒绝或明确确认；Task 1/5 覆盖子树 token 与草稿保护。
5. 管理页切换、改名、重新登录不得重置草稿或文本撤销历史；Task 2/5 覆盖状态协调，界面手感留人工确认。

## Task 1：FastAPI 账号、成员和结构接口

**Files:** 修改 `src/server/app.py`、`schemas.py`、`serialization.py`；新增 `routes_accounts.py`、`routes_members.py`、`routes_nodes.py`、`dependencies.py`；扩展 `tests/test_server_api.py` 或新增复用 ServerFixture 的 `tests/test_server_management.py`。

**Interfaces:** ServerServices 增加 `accounts: AccountService` 与 `access: AccessService`，共享已有 Database。`session_identity(request)` 返回经过会话与 CSRF 校验的 RequestIdentity；严格 body 依赖复用 read_json_object/read_query/validate_body。接口及字段逐项遵循 spec 中 HTTP 表。

- [ ] 在现有真实数据库 fixture 上增加管理生命周期用例：管理员创建账号、匿名激活、普通用户读取自己的会话、修改显示名；普通用户管理账号返回 403；客户端伪造角色／身份未知字段返回 422。
- [ ] 增加改密用例：旧会话失效、成功响应删除 Cookie、旧密码不能重新登录、新密码可以登录；成员 editor 能新建但不能修改成员角色；站点管理员非工作区成员不能管理成员。
- [ ] 增加文件用例：新建文档与文件夹、改名后正文与 revision_id 不变、过期结构版本返回 409、无 CSRF 写入返回 403；准备目录删除后新增子节点，旧 subtree_token 删除返回 409且内容仍存在。
- [ ] 分组注册同步路由，直接调用应用服务；新增 strict schemas 和公开序列化。账号启停及角色操作仅允许声明的路径，不使用任意 getattr 动态执行。所有版本采用 StrictInt，enabled/recursive 采用 StrictBool。
- [ ] 运行 `python -m unittest tests.test_server_api tests.test_server_management tests.test_server_transport tests.test_server_production -q`（若用例全部扩展原文件则移除 management 模块项）；通过后提交本任务文件。

## Task 2：前端请求与操作状态

**Files:** 修改 `frontend/src/api/types.ts`、`client.ts`、`state/session.ts`、`state/directory.ts`；新增 `composables/useManagement.ts`、`useFileOperations.ts`；扩展 `frontend/tests/client.test.ts`、`session.test.ts`、`directory.test.ts`，必要时新增 `operations.test.ts`。

**Interfaces:** ApiClient 提供 activate/resetPassword/changeProfile/changePassword/listUsers/createUser/userAction/setSiteAdmin/readMembers/addMember/setMemberRole/removeMember/createFolder/createDocument/renameNode/prepareDelete/deleteNode。公开返回值分别是 UserResponse、AccountGrant、WorkspaceResponse、NodeResponse、DocumentResponse、DeletePlan、OkResponse。AccountGrant 含 user/token/purpose/expires_at；DeletePlan 含 object_id/version/items/subtree_token。管理协调层绑定 session epoch，身份变化清除列表与一次性凭据。

- [ ] 扩展客户端用例，断言新增请求的真实 public path、body、nonce/CSRF 与严格响应解析；迟到管理成功／401不能写入新会话；changePassword 成功立即清除旧证明。
- [ ] 为目录刷新和草稿协调增加行为用例：刷新受影响父目录保留其展开状态；修改文档名称不替换正文或修订；改密后暂停编辑并保留草稿，匿名恢复失败可重试。
- [ ] 实现 schema validators 与上述 API 方法，复用 request；不自动重试写请求。SessionState 暴露安全的 profile 更新与已撤销会话恢复操作，保存 workspace_role 供入口展示，后台仍执行真实授权。
- [ ] 文件操作层统一确定新建父目录，确认丢弃时复用既有草稿流程；目录删除计划与最终删除严格一一绑定；成功删除后清理相关缓存与当前选中，失败保留草稿。
- [ ] 运行 `npm --prefix frontend test`、`npm --prefix frontend run typecheck`；通过后提交本任务文件。

## Task 3：CodeMirror 实时预览与阅读渲染

**Files:** 修改 `frontend/package.json`、`package-lock.json`；新增 `editor/livePreview.ts`、`editor/commands.ts`、`components/MarkdownEditor.vue`、`components/MarkdownPreview.vue`、`markdown/render.ts`；新增 `frontend/tests/markdown.test.ts` 和必要的简短编辑器行为用例。

**Interfaces:** MarkdownEditor props 为 `modelValue: string`、`documentId: string`、`readonly: boolean`、`sourceMode: boolean`，emit `update:modelValue(text: string)`。MarkdownPreview 接收 `source: string`，渲染前净化。`livePreview` 扩展由 Markdown syntaxTree 与当前选区生成 Decoration；commands 对 EditorView 执行真实文本事务。

- [ ] 安装兼容现有 Node/Vite 的 CodeMirror state/view/commands/language/lang-markdown 和必要解析依赖、Marked、DOMPurify，锁定版本；复用已有 Node 测试环境，不在仓库添加独立验证项目。
- [ ] 添加关键行为测试：非活动粗体／标题标记隐藏，选区相交时显露；选区跨行时不隐藏范围内标记；展示构建不修改 doc.toString；工具栏操作可以撤销；只读不能通过用户编辑事务写入。
- [ ] 建立 CodeMirror 一次性 mount 与 destroy，watch 文档身份与外部正文，用户回传相同正文不再次替换；文档切换重新建立撤销历史，保存回传同文档保持用户继续输入。支持视图 readOnly/editable 重配置与组合输入保护。
- [ ] 实现标题、强调、删除线、列表、任务项、引用、链接和代码样式；活动语法显露源码。表格与未知语法保留源码，独立阅读模式显示 GFM 表格；链接不在编辑中自动导航。
- [ ] 阅读渲染复用旧预览的禁止标签／属性、链接白名单和图片 alt 占位。增加恶意脚本、危险链接、代码块与中文标题的真实渲染净化测试，利用现有外部 jsdom（如需路径则通过环境变量指定）。
- [ ] 运行相关前端测试、typecheck、build，通过后提交本任务文件。

## Task 4：统一主题、登录和账号页面

**Files:** 修改 package/lock、`vite.config.ts`、`src/main.ts`、`styles.css`、`App.vue`、`LoginPanel.vue`；新增 `uno.config.ts`、`src/theme.ts`、`pages/AccountPage.vue`、`AdminUsersPage.vue`、`MembersPage.vue`。

**Interfaces:** 根 NConfigProvider 使用 theme.ts 的 shared tokens，UnoCSS 引用同一 CSS 变量。页面 props/emits 使用 Task 2 的 User、AccountGrant、WorkspaceResponse，业务请求从协调层统一调用。App 保留现有会话失败处理与 editor 实例。

- [ ] 安装 Naive UI 与 UnoCSS，接入 presetWind3、Vite 插件和虚拟样式导入；使用按需显式组件 import，不加全局 reset 或额外框架。
- [ ] 建设统一浅色主题、顶栏、登录卡片与桌面／窄屏布局。登录包含登录、激活、重置入口，提交后清空密码／凭据；等待 bootstrap、nonce 或未初始化时不能错误提交。
- [ ] 建设个人页面、站点账号列表及操作、成员管理页面，分别显示角色并按身份提供入口。敏感操作确认，凭据可复制／清除且身份变化清除。撤销自己的管理权后同步当前身份和入口。
- [ ] 页面切换保留工作台和 CodeMirror 实例；持久展示请求错误，冲突后刷新版本供用户再次确认，不静默重复写入。
- [ ] 运行前端既有测试、typecheck、build，不为外观新增测试；通过后提交本任务文件。

## Task 5：文件菜单、编辑工作台集成与交付验证

**Files:** 修改 `DirectoryTree.vue`、`DocumentEditor.vue`、`ConflictPanel.vue`、`App.vue`；新增 `components/FileActions.vue`；更新 `docs/工作台开发.md`、`README.md`；新增 `docs/verification/2026-10-07-vue-workbench.md`。

**Interfaces:** 目录行使用 Task 2 的 node access.actions 决定操作显示，FileActions 输出明确的操作和目标 Node，不推导当前身份。DocumentEditor 消费 Task 3 MarkdownEditor 并继续向 EditorState 发 edit 事件；源码／阅读是次级展示选项。

- [ ] 接入目录右键和可见菜单按钮，根新建、文件／文件夹的目标位置、重命名输入和删除确认均调用真实接口；目录刷新失败与写入成功分别说明，避免再次重复创建。
- [ ] 删除当前文档或包含当前文档的目录之前执行草稿保护；删除计划 items 用于判断包含关系，旧计划冲突要求重新确认；同名冲突保留输入供修改。
- [ ] 集成格式工具栏、默认实时预览、次级源码／阅读菜单、只读阅读、冲突参考与状态提示。外层加载／保存忙碌不能无故禁止正文输入，保留既有保存禁用条件。
- [ ] 执行 `npm --prefix frontend test`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`；执行相关 Python adapter、服务与架构测试，若无未决风险不追加无关全套验证。
- [ ] 用独立临时库实际启动 Vite 5173 和 FastAPI 8001，检查 Vue/UnoCSS 模块、/api/healthz、bootstrap。若可用浏览器，仅截图查看桌面与窄屏；不运行自动登录／保存／右键交互。
- [ ] 记录命令、结果、截图和未验证项，更新开发文档说明接口分组、主题、实时预览维护位置；执行 git diff --check，提交实现并进行最终代码审阅。

## 执行方式建议

建议本会话直接实施，任务之间按上述顺序推进。接口与草稿状态关联较紧，集中实现可以减少交接成本。用户若选择委派，子任务只执行此已确认路线，并明确文件所有权，不授权新的架构选择。
