# Vue + FastAPI 最小前后端闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution, or superpowers:subagent-driven-development only if the user selects delegation. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 已有账号通过 Vue 登录、浏览目录、读取和保存文档，刷新后读取持久化结果，并正常退出。

**Architecture:** 新增独立 FastAPI 适配器，直接调用 IdentityService 和 ContentService；内容操作仍在现有应用工作单元中完成认证、授权和事务。Vue 单页通过 Vite /api 代理接入新入口，草稿、会话和请求状态按职责拆分，旧工作台保留。

**Tech Stack:** Vue 3、TypeScript、Vite、npm；FastAPI、Uvicorn、Pydantic（FastAPI 依赖）；SQLite、argon2-cffi；unittest + HTTPX ASGITransport；前端纯逻辑使用 Node 内置测试，不新增浏览器测试框架。

**Spec:** [已批准设计](../specs/2026-10-07-vue-fastapi-minimal-loop-design.md)。

状态：已按用户批准的路线完成六项实施及审阅。Python 回归、前端状态检查和真实 HTTP 代理闭环通过；typecheck/build 复用未改动代码的最终成功记录。登录页截图超时，视觉检查和浏览器完整交互未验证；实际证据见文末。

## Global Constraints

- 用户确认的正式技术路线为 Vue + FastAPI；新适配器不得包装旧 API.dispatch。
- 不变更数据库 schema，不引入 ORM、异步数据库驱动、组件库、全局状态库或前端路由库。
- 普通启动不创建、播种、升级或修复数据库；使用指定的已初始化协议 2 WAL 库。
- 所有请求的身份和权限由现有服务判断；固定默认 scope，客户端不能指定授权身份或访问范围。
- JSON 请求体上限 2 MiB（2097152 字节），按实际读取字节限制；拒绝重复键、重复查询参数、未知字段及不合法类型。
- Cookie 为 HttpOnly、SameSite=Strict、Path=/；本地 HTTP 不设 Secure，配置保留 HTTPS 选项。
- 登录使用 X-C156-Nonce；退出和保存使用 X-C156-CSRF。Host/Origin 显式允许；不开启 CORS，不信任转发来源头。
- 同步服务及密码计算在线程池执行；每次操作独立短事务，不共享数据库连接。
- 开发运行两个 loopback 进程、单后端 worker；本阶段不做公网部署或静态托管。
- 草稿只驻留内存，不写 localStorage；保存失败、冲突、身份变化及过期响应不能静默丢失或覆盖草稿。
- 前端样式从简：系统字体、浅色背景、目录栏、文档标题、文本编辑区和状态提示。不做视觉方案探索或额外样式打磨。
- 页面验证最多打开截图，不自动点击、填表或执行浏览器交互。
- 永久测试只覆盖新入口和新状态逻辑的实际风险；复用既有夹具，低风险文案/样式/配置不新增测试。
- 不改动原始 data 样本、用户运行库及既有未提交的身份设计文档；不自动推送、合并或部署。

## Review Focus

1. 请求已经提交但客户端未收到响应：保留正文和原修订，标记结果未确认；重试不能覆盖并发更新（Task 4）。
2. 账号或选中文档变化后迟到的响应：不得更新新会话、新目录或新文档状态（Task 4/5）。
3. Vite 代理保留浏览器 Origin 与 Host：真实代理读 API 应成功，非法来源仍拒绝（Task 2/6）。
4. 中文、CRLF 与末尾空行：显示规范化不能触发无意义保存，手动输入及保存结果应准确（Task 4）。
5. 实际请求体超过上限但头部不可信：流式读取中终止并返回 413，不先完整加载或调用业务服务（Task 2）。

## 文件与责任

| 文件 | 责任 |
| --- | --- |
| src/server/{__init__,app,config}.py | 应用工厂、lifespan、显式配置和服务组合 |
| src/server/{transport,auth}.py | 有界 JSON/查询校验、Cookie、Host/Origin、nonce/CSRF |
| src/server/{schemas,routes,serialization,errors}.py | 严格请求模型、7 个接口、公开响应和错误映射 |
| src/server/__main__.py、run_server.py | 单 worker loopback 启动入口 |
| requirements.txt、requirements-dev.txt | 固定后端运行/测试直接依赖；保留现有密码依赖 |
| frontend/{package.json,package-lock.json,index.html,tsconfig.json,vite.config.ts} | 前端工程和代理配置 |
| frontend/src/{main.ts,App.vue,styles.css} | Vue 启动、页面协调和最小样式 |
| frontend/src/api/{types,client}.ts | 前后端契约、响应解析和请求 |
| frontend/src/state/{editor,session,directory}.ts | 草稿/会话/目录状态，不访问 DOM |
| frontend/src/components/{LoginPanel,DirectoryTree,DocumentEditor,ConflictPanel}.vue | 基础表单、目录、编辑及手动合并 |
| tests/test_server_{startup,transport,api}.py | 新入口的真实 ASGI 行为验证 |
| tests/helpers.py、tests/test_architecture.py | 复用临时库夹具；扩展新适配器依赖约束 |
| frontend/tests/{client,editor,session,directory}.test.ts | 新生产状态逻辑的行为测试 |
| README.md、docs/工作台开发.md、docs/架构分层.md、.gitignore | 新旧入口、运行命令和目录约束 |

按下面顺序执行，不并行修改共享接口。每项完成后提交该项的明确文件；禁止 git add . 纳入无关改动。

### Task 1：可启动的 FastAPI 应用与显式配置

**Files:** 创建 src/server/{__init__,config,app,__main__}.py、run_server.py、requirements-dev.txt、tests/test_server_startup.py；修改 requirements.txt、tests/test_architecture.py，必要时在 tests/helpers.py 增加小型共用 server fixture。

**Interfaces:**
- 产生 `ServerConfig(database_path: Path, host: str = '127.0.0.1', port: int = 8001, allowed_hosts: tuple[str, ...] = ('127.0.0.1:8001', 'localhost:8001', '127.0.0.1:5173', 'localhost:5173'), allowed_origins: tuple[str, ...] = ('http://127.0.0.1:8001', 'http://localhost:8001', 'http://127.0.0.1:5173', 'http://localhost:5173'), cookie_secure: bool = False)`，不可变配置。
- 产生 `create_app(config: ServerConfig) -> FastAPI`；lifespan 在线程池验证库和默认拓扑，构造 `ServerServices(content: ContentService, identity: IdentityService, scope: ContentScope, nonce: str)` 保存到 app.state.services。
- 产生 `python run_server.py --database PATH --port 8001` 与 `python -m src.server`。CLI 接受重复 --allowed-host/--allowed-origin 参数覆盖允许列表；--help 不打开数据库。默认路径与旧入口一致，默认单 worker，禁用 Uvicorn access log 和 proxy_headers。
- 未显式覆盖允许列表时，CLI 根据实际后端 port 生成 loopback Host/Origin，保留 Vite 5173 来源；覆盖时以用户提供列表为准。配置拒绝非 loopback 监听地址和非法端口，本阶段不开放公网监听。

- [x] 核对当前 Python 3.13.2/Node 22.22.1，读取官方包元数据选取兼容稳定版本；固定 FastAPI/Uvicorn/HTTPX，保留 argon2-cffi==25.1.0。安装依赖不升级系统全局环境，使用隔离虚拟环境。
- [x] 编写 `test_missing_database_is_not_created`、`test_incompatible_database_is_not_repaired`、`test_initialized_library_starts`、`test_help_does_not_open_database`：真实临时库，断言启动成功/失败和目标文件状态，不访问用户库。
- [x] 扩展已有架构检查：server 不导入旧 web/cli/editor/file、Repository 或 sqlite3；services/core/storage 不依赖 server、FastAPI 或 Starlette。
- [x] 实现工厂、配置、lifespan、启动入口和明确的初始化/引导错误指引。测试通过 `async with app.router.lifespan_context(app)` 执行启动，不依赖 HTTPX 自动运行 lifespan。
- [x] 验证 `python -m unittest tests.test_server_startup tests.test_architecture -v`；预期通过。再运行 `python run_server.py --help`，预期显示参数且无运行库写入。
- [x] 提交 `feat(server): add explicit FastAPI application entry`。

### Task 2：认证与有界传输

**Files:** 创建 src/server/{transport,auth,schemas,errors,serialization,routes}.py、tests/test_server_transport.py、tests/test_server_api.py；修改 src/server/app.py。

**Interfaces:**
- `read_json_object(request: Request) -> dict` 为 async，仅流式读取/解析；实际超过 2097152 字节返回 413，重复键/不合法 JSON 返回 400。
- `read_query(request: Request, *, required: tuple[str, ...] = ()) -> dict[str, str]` 拒绝缺失/未知/重复字段。
- `RequestIdentity(session_token: str | None, source: str)` 与 `parse_identity(request: Request) -> RequestIdentity`；不导入旧 auth 模块。
- `require_login_nonce(request, services) -> None`、`require_session_csrf(request, services, identity) -> None`；涉及同步服务的校验放入同步 dependency/线程池。
- `LoginBody`、`EmptyBody`、`SaveDocumentBody` 使用 Pydantic 严格字段、extra='forbid'，模型校验错误统一为 422/invalid_request，不返回验证器原始 input。
- 建立 GET /api/bootstrap、GET /api/session、POST /api/auth/login、POST /api/auth/logout，返回设计中的字段；成功登录不在 JSON 返回 session_token。
- 错误统一 `{error: {code, message, details: {}}}`：401 清 Cookie；403 拒绝；409 冲突；429 带 Retry-After；503 暂忙；500 内部错误。未知 API 为 404，已注册路径错误方法为 405；API 响应均 no-store。

- [x] 扩展小型真实夹具：initialize_database → bootstrap_admin → 正常登录；身份服务签发 token 仅在测试内保留。使用 `unittest.IsolatedAsyncioTestCase`、HTTPX ASGITransport 和显式 lifespan；测试 source 固定 loopback。
- [x] 添加 `test_login_session_logout_cookie_contract`：bootstrap nonce → login → session 同一 user → logout → session 401；Cookie 含 HttpOnly/SameSite=Strict/Path=/，失败统一提示。
- [x] 添加 `test_host_origin_nonce_and_csrf`、`test_invalid_cookie_is_not_guest`：合法 Vite Origin/Host 成功，非法 Host/Origin/null/missing nonce/错误 CSRF 403；重复或失效 Cookie 401 并清除。Secure 配置由一次针对性 Cookie 行为检查覆盖。
- [x] 添加 `test_json_and_query_are_strict`、`test_oversize_stream_without_trusted_length`：重复 JSON 键、非有限值 400；未知字段/数值代替字符串 422；重复查询 400；用 HTTPX 异步字节生成器超过 2 MiB 返回 413。对认证行为使用真实服务，不复制领域算法。
- [x] 实现纯 ASGI 传输防护、独立 auth/serialization/error 模块及认证路由；不要用宽泛 BaseHTTPMiddleware 预读无限请求体。Host/Origin 校验对错误响应也返回统一 JSON。关闭自动 /docs、/redoc 和 OpenAPI HTTP 路由，避免为最小入口增加未保护页面。
- [x] 日志仅记录方法、固定路径、状态或异常类型；请求校验、通用异常及启动错误不得记录凭据/正文。错误测试使用哨兵敏感输入并断言响应和捕获日志没有该输入。
- [x] 验证 `python -m unittest tests.test_server_transport tests.test_server_api -v`；预期通过。
- [x] 提交 `feat(server): add session authentication and bounded transport`。

### Task 3：目录与文档持久化闭环

**Files:** 修改 src/server/{routes,schemas,serialization}.py、tests/test_server_api.py。

**Interfaces:**
- GET /api/children?folder_id=ID 调用 `ContentService.list_children_with_access(scope, folder_id, session_token=token)`。
- GET /api/document?object_id=ID 调用 `read_document_with_access`。
- PUT /api/document 调用 `save_document_with_access(scope, object_id, content, expected_revision_id=revision, session_token=token)`；只发送正文，无 metadata/scope/角色输入。
- 节点、文档和 access 输出字段与已批准设计及旧 serialization 的公开字段一致，ID 不进行 UUID 假设。

- [x] 添加 `test_read_save_read_persists`：通过现有服务创建包含中文/末尾空行的文档，API 读取 r1 → 保存新正文 → 新客户端读到新正文/r2；再从 ContentService 读取一致结果。
- [x] 添加 `test_stale_revision_preserves_server_content`：两个读取基础相同的请求，第一次保存成功，第二次旧修订保存返回 409/conflict；再次读取仅有第一次正文。
- [x] 添加 `test_hidden_read_and_revoked_write`：通过现有 AccountService/AccessService 配置成员或私密对象；新入口不能泄露隐藏正文，撤销编辑权后保存失败且内容未变化。冻结文档的失败检查合并在同一测试文件，避免跨层重复权限组合。
- [x] 实现三个同步路由和文档序列化，沿用 services 的授权与事务，不在适配器再次实现 AccessPolicy。
- [x] 验证 `python -m unittest tests.test_server_api tests.test_content_access tests.test_content_concurrency -v`；预期通过。
- [x] 提交 `feat(server): expose authorized directory and document operations`。

### Task 4：前端客户端与草稿状态

**Files:** 创建 frontend/{package.json,package-lock.json,tsconfig.json,index.html,vite.config.ts}、frontend/src/api/{types,client}.ts、frontend/src/state/{editor,session}.ts、frontend/tests/{client,editor,session}.test.ts；修改 .gitignore。

**Interfaces:**
- `ApiClient`：`bootstrap()`、`login(loginName, password)`、`session()`、`logout()`、`listChildren(folderId)`、`readDocument(objectId)`、`saveDocument({object_id, content, expected_revision_id})`、`invalidate()`；具名 Bootstrap/Session/Document/Node/Access 响应类型来自 Task 2/3。
- API 客户端使用 credentials:'same-origin'；持有 nonce/csrf 及身份 epoch。`ApiError(code: string, message: string, status: number)` 区分 network/response/stale/domain error。
- `EditorState` 的主要接口沿用现有纯状态行为：`setIdentity(userId, {discard?})`、`open(snapshot)`、`edit(text)`、`beginLoad()/finishLoad(ticket,snapshot)`、`beginSave()/saveSucceeded(ticket,snapshot)/saveFailed(ticket,code)`、`beginLatest()/setLatest(snapshot,epoch,ticket)/startMerge(ticket)`；提供 dirty/hasUnsavedWork/shouldWarnBeforeUnload/paused/uncertainSave/revision/draft/comparisonDraft。
- `SessionState` 提供 `bootstrap()`、`login(loginName,password)`、`logout()`、`expire()`；接受 ApiClient 和 EditorState，只持有身份与根信息，不操作 DOM。返回换账号草稿阻塞状态，由组件要求明确丢弃后再接纳新身份。
- 登录成功后重新 bootstrap 获取根及授权信息；expire/退出后先取得匿名 bootstrap nonce，再允许重登。所有异步会话操作也绑定 epoch；旧身份请求不能覆盖当前身份，登录接纳被草稿阻塞时不得展示另一用户的目录。
- npm scripts 固定为 dev、typecheck（vue-tsc --noEmit）、build（vite build）、test（node --experimental-strip-types --test tests/*.test.ts）。纯 TypeScript 使用可擦除类型和显式 .ts import，避免 Node 测试加载 .vue。

- [x] 固定 Vue/Vite/TypeScript/@vitejs/plugin-vue/vue-tsc/@types/node 兼容版本并生成 npm lockfile；添加 node_modules、*.tsbuildinfo 忽略。Vite host=127.0.0.1、port=5173、strictPort=true，/api 目标为 http://127.0.0.1:8001、changeOrigin=false。
- [x] 在前端 tests 中迁移适用于新生产状态逻辑的关键场景，不导入旧 JS：保存期间新增输入保留、迟到响应隔离、409 原草稿/修订保留、同用户重登继续、跨账号阻塞、CRLF 初始不脏、末尾空行保留、合并前不推进基础。
- [x] 添加网络不确定保存场景：`saveFailed(ticket,'network')` 后 draft/原 revision 保留、uncertainSave 和关闭提醒为 true；重试仍用原修订。迟到成功不能清除另一个用户的草稿保护。
- [x] 客户端只 mock fetch 传输，断言公开路径/正文/认证头和可观察错误；测试旧 epoch 的响应/401 不能改变新会话。SessionState 测试调用真实生产状态，覆盖登录成功但身份接纳被旧草稿阻塞的状态。
- [x] 实现客户端响应最小结构校验与生产状态类，参考旧算法但独立放入新工程；不让模板用 any 掩盖契约问题。状态经 Vue reactive 包装时保留对象 ticket 身份一致性（使用 ID/代次或 toRaw），避免代理导致保存票据失配。
- [x] 验证 `npm --prefix frontend run test`、`npm --prefix frontend run typecheck`；预期通过。工程未提供完整 Vue 页面前，typecheck 使用已有 ts 文件和基础入口，不用伪造页面行为测试。
- [x] 提交 `feat(frontend): add typed API and protected draft state`。

### Task 5：最简共享文档页面

**Files:** 创建 frontend/src/{main.ts,App.vue,styles.css}、frontend/src/state/directory.ts、frontend/src/components/{LoginPanel,DirectoryTree,DocumentEditor,ConflictPanel}.vue、frontend/tests/directory.test.ts；修改 frontend/index.html。

**Interfaces:**
- `DirectoryState(client)` 提供 `setRoot(root, userId)`、`loadChildren(folderId)`、`toggle(folderId)`、`reset()`；缓存以当前身份代次隔离，迟到响应不能重新填充已清空目录。
- App 负责 SessionState/EditorState/DirectoryState 的协调，组件通过 props/emits 调用；输入密码只留在登录表单，用完清空。
- EditorState 生命周期不随 LoginPanel/工作台条件切换销毁；会话失效页面仍提供原草稿的只读查看/复制区域。

- [x] 添加 `directory.test.ts`：真实 DirectoryState 配合受控传输，展开返回节点；身份改变后旧响应忽略；读取失败不伪装为空目录。只在此层覆盖目录竞态。
- [x] 实现登录和 bootstrap 状态：加载中/失败可重试、无权限根明确提示；401 只对当前 epoch 触发 expire，不清草稿。目录只展示服务已授权节点。
- [x] 实现目录选择和编辑：未保存/结果未确认/合并参考正文存在时，选择与退出先确认保留或明确丢弃；保存期间禁止切换，保留继续输入能力；只读编辑区为 readonly。
- [x] 实现保存按钮与 Ctrl/Cmd+S，显示未保存/保存中/已保存/结果未确认/权限受阻；注册并卸载 beforeunload 监听，只依据生产状态提醒。
- [x] 实现冲突区：查看最新正文不改草稿或基础修订；开始合并需要明确操作，新正文进入编辑区，原草稿独立只读展示。手动输入并保存后清除参考；失败保留两份内容。
- [x] 实现最低限度 CSS：系统字体、浅色背景、普通按钮和边框；桌面目录侧栏+正文，窄屏上下排列，长标题换行、编辑区不溢出。使用正常 label、焦点和禁用状态，不引入图标/字体/图片资源或动画。
- [x] 验证 `npm --prefix frontend run test`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`；预期通过。不为样式、文案或模板结构增加永久测试。
- [x] 提交 `feat(frontend): connect minimal document workspace`。

### Task 6：集成检查与使用说明

**Files:** 修改 README.md、docs/工作台开发.md、docs/架构分层.md，必要时修正前述接线文件；更新本计划完成状态与证据。

**Interfaces:** 新入口开发运行方式与旧入口分别说明；旧运行方式和管理命令不被新前端启动替换。

- [x] 文档给出虚拟环境安装 requirements.txt/requirements-dev.txt、npm --prefix frontend ci、显式 init/bootstrap-admin、两个进程启动命令。示例库使用独立临时路径，不自动把演示数据写入默认库。
- [x] 使用测试 fixture 在 /tmp 准备临时已引导库并通过 ContentService 写入示例文档；启动真实 Uvicorn 与 Vite，执行一次性 HTTP 代理检查：bootstrap→nonce→login→children→read→save→read→logout； Cookie 只保存在临时内存/受限临时文件，凭据不进入命令行/日志。HTTP 客户端检查不使用浏览器点击。
- [x] 代理请求携带 http://127.0.0.1:5173 Origin；断言合法请求成功，非法 Origin 403。必要时修正允许配置，不能用移除 Origin 校验解决失败。记录真实命令与结果，不把进程内 ASGI 测试当作代理集成证据。
- [x] 运行 `python -m unittest discover -s tests -v`、`node --experimental-default-type=module --test tests/web/*.test.mjs`。旧预览实际净化若缺 jsdom，按既有文档配置；无法运行时明确记录未验证，不删断言。
- [x] 运行 `npm --prefix frontend run test`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`；此前结果仅在代码未变化时复用，避免无理由重复运行。
- [x] 如截图环境可用，只打开页面并截图检查登录页；工作台以一次性页面状态 fixture 截图注明其为展示样本，不冒充登录交互验证。没有截图条件时报告未验证；不为截图搭建永久浏览器辅助项目。
- [x] 执行 `git diff --check` 并审阅变更范围/依赖方向。结束本任务启动的进程，记录测试结果、构建结果、截图范围，以及未运行浏览器完整交互和公网部署。
- [x] 提交 `docs: document Vue FastAPI local workflow and verification`。

## 自检与执行交接

- 设计第 1/2 节范围对应 Task 3/5/6；第 3 节边界对应 Task 1/4；第 4/5 节接口与传输对应 Task 2/3；第 6 节启动对应 Task 1/6；第 7 节草稿对应 Task 4/5；第 8 节验证对应各任务及 Task 6。
- 前端不追求视觉打磨，Task 5 的交付只包含必要布局、表单和明确状态。Markdown 预览及实时协作不混入本阶段。
- 测试命令以执行环境中的隔离 Python 解释器运行；实际解释器路径和依赖版本在执行记录中保存。这里的 python 为简写，不要求使用全局 Python。
- 用户选择 subagent 实施。主 agent 逐项派发实现和独立审阅，不并行写共享接口；权限、所有权和本计划约束必须完整交接。
- 计划审阅并选择执行方式后才开始写产品代码；实施已完成；各项实际检查与限制见下面执行证据。

## 执行证据（2026-10-07）

Task 1–5 实现及修复已完成并通过独立审阅，提交范围 cc1e8a7..4866fd5；各任务命令、结果和修复见 `.superpowers/sdd/2026-10-07-vue-fastapi-minimal-loop/task-{1..5}-report.md`。

Task 6 的实际证据见 `task-6-report.md`：Python 全回归477项通过；旧 Node检查含真实jsdom净化通过；新前端四测试文件通过。typecheck/build复用4866fd5的成功证据（产品代码未变化）。真实Uvicorn8001与Vite5173完成一次性HTTP代理登录、目录、读取、保存、再次读取和退出，非法Origin403。独立临时库及启动进程已清理。

截图条件已尝试：Chrome仅打开登录页，但20秒超时且无图片，截图视觉检查未验证。该可选步骤已以明确限制结束；没有执行浏览器点击、填表、完整交互或公网部署。
