# Vue + FastAPI 最小前后端闭环

日期：2026-10-07。状态：用户已批准，进入实施计划，尚未实施。

## 1. 目标与已确认路线

长期网站采用 Vue + FastAPI。现有 Python 标准库 HTTP 工作台是验证内容内核的临时入口。用户已确认新增独立 FastAPI 适配器，直接调用现有应用服务，不包装旧 API.dispatch。

本阶段使已有账号可以在 Vue 页面登录、浏览有权限的目录、打开文档、修改并保存，刷新后读到保存结果，并退出登录。权限检查、正文冲突检查及失败草稿保护必须延续。

本文件的依赖、模块组织和开发运行方案作为实施计划依据。

## 2. 范围

交付：

- 登录、会话恢复和退出；已有账号与显式管理员引导流程复用现有实现。
- 默认工作区 main 内容树的目录浏览和文档读取。
- Markdown 源码文本编辑、手动保存、保存状态和未保存提醒。
- 冲突时保留本地草稿，按需读取最新正文供比较；用户明确开始手动合并后，以最新修订为基础编辑，保留原草稿供参考。
- 会话失效暂停保存，同用户重登可以继续；其他用户不能接手原用户草稿。
- 本地开发启动说明、API 行为验证、前端检查和构建。

后续阶段：实时协作、自动保存、跨刷新草稿恢复、Markdown 预览、新建与结构操作 UI、账号与访问管理页面、激活/重置页面、分支、审核发布、媒体及公网部署。

旧工作台继续保留，提供现有管理操作。新入口不承诺与旧入口共享登录 Cookie；两者的账号、内容和权限数据共享指定的数据库。测试不操作用户运行库。

## 3. 技术方案与边界

前端提案：Vue 3、TypeScript、Vite，使用 npm 和提交的 lockfile；本阶段不引入组件库、全局状态库或前端路由库。登录和工作台作为单页的两种状态，按职责拆组件和 composable。

后端提案：FastAPI、Uvicorn；接口测试使用 HTTPX 的进程内 ASGI 客户端，延续 unittest。实施时核对 Python/Node 兼容性并固定直接依赖版本。保留 argon2-cffi、现有 SQLite schema 和 Repository，不引入 ORM、异步数据库驱动或数据库迁移。

```text
frontend/ Vue 页面与状态
  → /api HTTP JSON
src/server/ FastAPI 请求与响应适配
  → IdentityService / ContentService
  → ApplicationUnitOfWork / AccessPolicy
  → 现有 Repository / Database / SQLite
```

- frontend/：独立前端工程，拥有页面、API 客户端、类型、会话与草稿状态。
- src/server/：应用工厂与配置、路由及请求模型、Cookie/CSRF、传输校验、序列化与错误映射，按职责拆文件。
- src/services/：继续拥有身份认证、授权、业务规则与事务，不依赖 FastAPI 或 src/server。
- src/storage/：继续负责连接、事务和持久化；服务器不直接查询 Repository 或拼接 SQL。
- src/web/：旧入口保持现状。可参考其契约和纯状态算法；新服务器不导入旧路由分发器，前端不跨目录导入旧页面运行脚本。

复用不等于跨入口互相依赖。本阶段允许新适配器有少量独立序列化代码，不为消除重复重构全仓。

## 4. API 契约

仅接入闭环所需接口，路径与主要 JSON 字段沿用当前契约：

| 方法与路径 | 输入 | 响应 |
| --- | --- | --- |
| GET /api/bootstrap | 无参数 | 未登录返回 initialized、nonce；已登录返回会话、root、root_access、workspace_role、workspace_access_version |
| POST /api/auth/login | login_name、password | user、csrf、expires_at；通过 Set-Cookie 签发会话 |
| GET /api/session | 无参数 | user、csrf、expires_at |
| POST /api/auth/logout | 空 JSON 对象 | ok；撤销会话并清除 Cookie |
| GET /api/children | folder_id | nodes，每个节点携带 access |
| GET /api/document | object_id | document、access |
| PUT /api/document | object_id、content、expected_revision_id | document、access |

对象 ID 作为不透明字符串处理。服务端固定默认 scope，不接受客户端提供 workspace_id、branch_id 或 root_id。客户端传入的用户、角色、权限不能成为授权依据。

输入模型使用严格字段类型，拒绝未知字段、缺失必需字段、重复查询字段和重复 JSON 键、非有限数值及不合法 JSON。JSON 请求体上限沿用 2 MiB，按实际读取字节限制，不能只信 Content-Length；ASGI 不照搬旧服务器禁止 Transfer-Encoding 的传输限制。

## 5. 身份与传输

- 沿用随机不透明会话令牌和数据库摘要，Cookie 为 HttpOnly、SameSite=Strict、Path=/；本地 HTTP 开发不设置 Secure，配置预留 HTTPS 的 Secure 选项。
- 未登录 bootstrap 提供进程级 nonce，登录提交 X-C156-Nonce；退出和保存提交当前会话的 X-C156-CSRF。比较采用恒定时间方法。
- 重复、格式错误或失效的会话 Cookie 必须被拒绝，不能降级为游客。登录请求带已有 Cookie 时也校验其有效性，失效则清除后重新发起。
- 配置明确允许的 Host 和 Origin；Origin 存在时严格匹配允许值，拒绝 null。开发允许值涵盖实际 Vite 来源；无 Origin 的非浏览器请求仍须通过 nonce/CSRF。
- 浏览器访问 Vite，Vite 将 /api 代理给 FastAPI，保留浏览器 Origin。不开启跨域 CORS。开发代理默认保留 Host，服务器允许已配置的前端和后端地址。
- 身份限流的 source 使用直连地址，不信任外部 X-Forwarded-For。本地代理请求会共用来源限流桶，此阶段接受；公网代理信任链另行设计。
- API 响应 no-store；认证令牌、密码、正文、完整查询参数不写请求日志。错误响应不暴露内部对象信息或异常堆栈。

本阶段开发运行使用单 worker；进程级 nonce 不构成未来多进程或多节点方案。

## 6. 服务执行与启动

应用工厂接收数据库路径和显式运行配置。启动仅验证既有数据库及默认拓扑，不隐式初始化、播种、升级或修复。缺失或不兼容时给出管理命令指引。启动期间允许使用现有 ApplicationUnitOfWork 确定固定范围；请求只调用公开应用用例。

现有服务与 sqlite3 都是同步实现。同步用例由 FastAPI 同步路由或明确的线程池边界执行，不在异步事件循环中直接执行数据库、密码散列或拓扑校验。每次业务操作仍由服务开启短事务，不共享连接，不让事务对象逃逸到请求之外。

开发启动 FastAPI/Uvicorn 和 Vite 两个进程，默认仅监听本机，可通过 SSH 转发访问。服务端入口明确区分旧 run_web.py 与新入口。前端构建产物可生成，但本阶段不增加静态托管、反向代理、Docker 或公网部署流程。

## 7. 前端行为

布局为账号区域、左侧目录、右侧文档标题/编辑区/保存状态。用户补充要求：前端样式先不打磨，采用最低限度的共享文档界面，尽量从简。使用系统字体、浅色背景、普通表单和清楚的状态提示，不加入品牌视觉、动画或复杂布局。只读文档可打开，编辑和保存按服务返回的 access 展示；最终授权仍由服务重新判断。

API 客户端负责凭据、CSRF、严格响应处理和统一错误；会话状态负责 bootstrap/login/logout；目录状态负责展开和选择；编辑状态负责正文、基础修订、在途保存、冲突和草稿归属。组件不承担服务端权限决策。

- 保存捕获发送时的正文和基础修订；保存过程中输入的新内容不被响应覆盖。
- 加载和保存响应绑定用户及文档选择代次，过期响应不能影响当前文档或登录状态。
- 切换文档前处理未保存内容；关闭/刷新页面通过 beforeunload 提醒，提醒不承诺浏览器一定拦截关闭。
- 409 conflict 保留草稿和基础修订，禁止静默覆盖。最新正文单独展示，明确确认手动合并后才推进保存基础。
- 401 暂停保存并保留草稿，同用户重登恢复；换用户需先复制或明确丢弃原草稿。
- 网络失败可能发生在服务器提交之后，展示结果未确认，不能标记已保存；后续重试保留原修订并正常处理冲突。
- 403/frozen 保留草稿并说明保存受阻。429 展示重试提示。5xx/503 展示服务错误，不能清空草稿。
- 登录页面也保留原用户草稿状态；退出如有未保存内容，先提示处理，不能无提示丢弃。

草稿只驻留内存；不把正文或认证令牌写 localStorage。

## 8. 验证与验收

使用临时数据库及真实服务，在最合适的层验证：

- FastAPI 适配器的登录/Cookie、会话恢复、目录读取、读取/保存/再次读取、退出失效。
- 新入口独有风险：CSRF/Origin、非法 Cookie、输入严格性和体积限制、授权失败、旧修订冲突以及错误信息不泄露。
- 草稿状态关键行为：保存期间的新输入、迟到响应、冲突保留、失效重登和账号切换隔离。参考已有场景，调用实际状态逻辑，不复制算法作预期。
- 扩展已有架构检查，使 src/server 与现有入口遵守同样的依赖方向，下层不能导入新服务器框架。
- 运行现有 Python 测试及旧工作台 Node 测试，前端类型检查和生产构建；仅因具体失败或新改动追加检查。
- 页面验证最多打开并截图；不自动点击、填写表单或执行浏览器交互验收。完整浏览器交互未经验证，交付时明确报告。

成功标准：新 Vue 页面通过新 FastAPI 入口形成持久化读写闭环，权限和修订冲突契约保留，旧 CLI/工作台及显式管理流程不受影响。测试通过是相关逻辑证据，不代替浏览器交互验收。

## 9. 方案取舍与资料

独立适配器比包装旧 API.dispatch 增加少量边界代码，但能独立使用 FastAPI 请求模型和路由，后续接 WebSocket 时不受旧工作台分发结构限制。

TypeScript 提供前端请求与状态的静态检查；本阶段保持少量依赖。Vite 开发代理使浏览器使用同源路径，但服务器仍需明确校验原始浏览器 Origin。

- Vue TypeScript：https://vuejs.org/guide/typescript/overview
- FastAPI 同步/异步执行：https://fastapi.tiangolo.com/async/
- Vite 开发代理：https://vite.dev/config/server-options
