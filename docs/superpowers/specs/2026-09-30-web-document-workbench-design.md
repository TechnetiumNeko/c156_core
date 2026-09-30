# 与 CLI 并列的 HTML／JS 文档工作台设计

日期：2026-09-30。状态：已实施；HTML／JS 工作台为本地试手与架构演示入口，与 CLI 共用现有 ContentService 和 SQLite 内核。

## 1. 目标与范围

用浏览器浏览虚拟目录、预览 Markdown、编辑并保存正文。Web 与 CLI 共用 ContentService、同一个运行数据库及相同的业务规则；网页不解析 CLI 命令、不调用终端编辑器、不直接访问 SQLite。

首版按本地开发工作台设计，使用纯 HTML、CSS、原生 JavaScript 和薄 Python HTTP 适配器。功能包括目录展开、打开文档、源码／渲染预览切换与并排查看、新建目录／文档、手动保存及 Ctrl／Cmd+S、保存状态、并发冲突及未保存正文保护。

沿用默认工作区、main 内容分支和 main 文件夹作为访问根。用户登录／权限、实时协作、分支切换、提交图、历史恢复、媒体上传、拖拽移动及删除界面仍属于后续阶段。首版不是所见即所得编辑器；LaTeX 与语法高亮扩展后续接入。

## 2. 分层与运行方式

```text
CLI 入口 ─────────────────────→ ContentService → Repository → SQLite
HTML／JS → HTTP／JSON Web 适配器 ─→ ContentService
终端 Editor 仍只接受 CLI 的纯文本输入并返回结果
```

当前目录：

```text
run_cli.py
run_web.py                    薄 Web 启动入口
run_demo.py                   独立示例库准备、Web 启动与浏览器打开
start_demo.sh                 仓库根目录启动脚本
src/
├── core/                     现有纯模型与规则
├── services/                 现有 ContentService
├── storage/                  现有 SQLite 存储及管理命令
├── cli/                      现有终端入口
├── editor/                   现有终端编辑器
└── web/                      与 cli 并列的 Web 适配器
    ├── __init__.py
    ├── __main__.py
    ├── app.py                服务器配置、生命周期、启动校验
    ├── http.py               请求路由、JSON 输入、HTTP 响应、静态资产白名单
    ├── api.py                请求字段到服务调用的适配，无业务规则或 SQL
    ├── serialization.py      纯快照到 JSON，显式解冻 metadata
    └── static/
        ├── index.html
        ├── styles.css
        ├── app.js            具名操作与集中事件接线
        ├── directory.js      目录缓存、选择、懒加载与 DOM
        ├── client.js         fetch 封装和结构化错误
        ├── editor-state.js   文档、草稿、保存／加载请求状态
        ├── preview.js        Markdown 渲染及净化
        └── vendor/           固定版本依赖、许可证与来源记录
```

首版选择标准库 ThreadingHTTPServer，避免为了本地入口新增 Python Web 框架；每个请求调用无会话状态的 ContentService，连接仍由每次服务操作独立管理。未来公开站点可替换 HTTP 适配器，服务与浏览器 JSON 契约保持独立。

```bash
python run_web.py --database data/c156.sqlite --port 8000
# 等价：python -m src.web --database data/c156.sqlite --port 8000
```

默认数据库路径同 CLI；启动先检查协议、WAL 和默认 scope，缺库或未就绪时提示现有管理命令并退出，不自动 init／导入／修改配置。首版仅绑定 127.0.0.1，打开 http://127.0.0.1:8000/；远程主机可由用户通过 SSH 转发访问。--help 不打开数据库。

快速演示另外使用 `python run_demo.py` 或 `./start_demo.sh`。该显式入口首次准备 `.c156/demo.sqlite` 并加入示例文档，后续启动保留修改；不导入或改写旧样本。默认打开浏览器，`--no-browser` 只启动服务；相同示例库已在指定端口运行时复用服务，端口被其他程序占用时选择空闲端口。它不改变常规 CLI／Web 的启动校验规则。

## 3. 页面与交互

桌面布局采用左目录树、中间 Markdown 编辑、右渲染预览；顶部为文档路径、未保存／保存中／已保存状态和保存按钮。只预览模式扩大阅读区域；窄屏将编辑／预览改为选项切换，目录面板可收起。

```text
┌────────────┬─────────────────────────────────────────────┐
│ 目录       │ 文档路径             状态       保存        │
│ 新建／刷新 ├───────────────────────┬─────────────────────┤
│ 作品       │ Markdown 源码         │ 渲染预览             │
│  └ 文档    │                       │                     │
└────────────┴───────────────────────┴─────────────────────┘
```

视觉以中文写作和长文阅读为主：浅灰蓝工作区、白色编辑纸面、深蓝灰正文和克制的蓝色操作强调；界面使用系统中文无衬线字体，预览提供中文衬线字体回退。避免卡片化堆叠和装饰性动画，保持明显键盘焦点、可辨错误与可读正文行宽。页面不显示 SQL、WAL、UUID 或底层存储词汇。

目录通过 children API 懒加载；按服务 position 展示，文件夹与文档使用清晰图标。刷新更新目录及路径，不自动覆盖正在编辑的正文。新建操作在用户选中的活动目录下执行，名称校验仍由服务决定；同名失败展示可读信息，保留输入。

输入在浏览器内更新预览，约 150ms debounce，不产生自动保存或新修订。手动保存、快捷键和切换文档时的确认均调用相同保存流程。未保存时切换文档提供“保存后切换／明确放弃／取消”；保存失败取消切换。关闭或刷新页面时，未保存正文、在途保存或待合并的对照草稿触发浏览器 beforeunload 提醒，明确说明草稿本阶段只在当前页面内存中。启动后自动打开根目录中的第一篇文档。

## 4. JSON API 契约

HTTP 服务启动时自行取得默认 scope，所有内容请求使用这个固定 scope。客户端不提供 workspace_id／branch_id／root_id，也不提供宿主机路径；出现这些未知参数报 400。对象 ID 是不透明字符串，通过 URLSearchParams 编码查询，不能假设旧 ID 全为 UUID。

| 方法与路径 | 输入 | 服务调用／返回 |
| --- | --- | --- |
| GET /api/bootstrap | 无 | default_scope、get_node；root 快照及当前服务器的写请求 nonce |
| GET /api/children | folder_id 查询参数 | list_children；nodes 数组 |
| GET /api/document | object_id 查询参数 | read_document；document 快照 |
| POST /api/folder | parent_id、name | create_folder；新 node，201 |
| POST /api/document | parent_id、name、content（默认空） | create_document；新 document，201 |
| PUT /api/document | object_id、content、expected_revision_id | save_document；提交后的 document，200 |

JSON 成功响应使用 `{ "node": ... }`、`{ "nodes": [...] }` 或 `{ "document": ... }`。Node 包含 id、kind、name、parent_id、position、version、path、created_at、modified_at、metadata；Document 再包含 content、revision_id。序列化显式取字段及 thaw_json，不直接 JSON 编码 FrozenDict，不携带数据库对象。

错误使用 `{ "error": { "code": "...", "message": "...", "details": {} } }`。领域错误复用现有 code，中文展示由前端处理；未知内部错误记入开发日志，对浏览器返回一般故障信息而不泄漏 SQL 或栈。

| 情况 | HTTP 状态 |
| --- | --- |
| 非法 JSON、缺少／未知字段、类型错误 | 400 |
| 名称／操作参数无效、错误对象类型 | 422 |
| 对象缺失／已删除 | 404 |
| scope 外对象、来源／nonce 检查失败 | 403 |
| 同名对象、正文 revision 冲突 | 409 |
| SQLite 锁等待超时 | 503 |
| 正文 JSON 请求超过 2 MiB | 413 |
| 未知内部故障 | 500 |

每次服务调用自行负责业务事务，HTTP 层不自行提交、不加入进程内锁、不自动重试冲突。所有确认、输入、Markdown 渲染和网络等待发生在数据库事务之外。HTTP 适配器不得导入 CLI、editor 或 Repository，也不包含 SQL；app 启动可以构造 Database／ContentService。

## 5. 编辑状态与并发保存

editor-state 保存当前 document_id、基础 revision_id、原始正文、textarea 初始呈现值、当前草稿、dirty、请求序号和保存状态。只允许一个目标文档保存请求在途；前端不以 disabled 按钮替代状态校验。

textarea 可能把 CRLF 转成 LF，因此无变化必须通过初始呈现值判断。没有修改时保持原始正文与修订，不把换行规范化误当成编辑；真实修改后使用编辑值保存。

保存发出时捕获目标 ID、基础修订与正文。响应回来后只更新对应文档：成功推进本次确认的基础修订；若用户在请求中继续输入，保留新草稿并继续标 dirty，不能用返回正文覆盖它。失败保持原正文草稿与原基础修订。加载不同文档时使用 AbortController／请求序号，旧请求不能覆盖后来选中的文档。

409 冲突时保留我的草稿与旧基础，允许查看最新正文、复制我的草稿，或进入显式手动合并。手动合并先读取最新快照，把最新正文及其 revision_id 作为新编辑基础，同时独立保留旧草稿供对照／复制；不能把旧草稿自动绑定最新 revision_id 强制保存。合并正文保存成功且没有继续输入后释放对照草稿；此前关闭或切换仍保护它。再次并发修改仍照常产生 409。

CLI 修改、移动或删除同一文档时，Web 保存使用现有服务的活动 scope、类型、修订检查；失败结果不绑定到同名替代对象。文档移动后路径按 ID 更新，不以旧路径作为对象身份。

## 6. 预览与本地入口约束

Markdown 使用固定版本 Marked 18.0.14，HTML 使用 DOMPurify 3.4.16 净化后插入预览，采用 HTML profile。第三方浏览器构建文件与许可证保存在 vendor，并记录来源、版本及 SHA-256；运行时不从 CDN 下载、不需要 npm 开发服务器。不修改正文来实现净化。

预览支持标题、列表、引用、链接、表格和 fenced code；代码以文字展示。脚本、事件属性、危险 URL、iframe／object／embed／form 等可执行或交互内容不进入预览。外部媒体自动加载暂不开放，保留图片 alt 描述；媒体上传与资源路由后续统一设计。

静态路由仅返回显式列出的前端文件，不能通过 URL 扫描或读取项目目录、data 或任意宿主机文件。响应配置 Content-Type、Content-Length、nosniff 与适当 CSP；目录和文件名用 textContent 呈现。

本地服务器校验 Host 为 loopback 名称，允许 SSH 转发产生的合法端口差异；写请求要求 JSON Content-Type、同源 Origin（浏览器提供时）及 bootstrap nonce 头。不开放 CORS。nonce 防跨站请求，不作为用户权限模型；首版不提供公网监听或部署流程。

## 7. 验证范围

- Python 标准库 unittest：临时数据库启动真实 HTTP 服务，检查静态资产、JSON、目录、创建、读写、scope 和错误映射；原旧样本只复制后用于迁移。
- HTTP 与独立服务／CLI 操作互相可见；两个请求基于同一 revision 保存只能一个成功，另一个 409，无丢失修订。
- 验证缺库／非 WAL／未知协议启动不修改目标，--help 不产生运行库；HTTP／静态路由不能访问 data、任意路径或 scope 外对象。
- Node 标准库测试独立 editor-state／client／preview 辅助：dirty、未修改 CRLF、加载竞态、保存中继续输入、冲突保留基础、显式合并及切换确认，不依赖浏览器自动点击。
- 预览恶意 Markdown 净化契约和固定资产来源可检查；API 测试真实保存，不能只验证 mock fetch。
- 页面验证默认仅打开并截图查看，不自动点击按钮、填写表单或执行浏览器交互流程。更深入浏览器集成测试需用户明确要求。
- 既有内核测试、架构边界检查、git diff --check 和旧样本散列继续通过；工作台仅补充真实 HTTP、编辑状态、客户端与净化所需验证。

启动、开发接线和验证命令见 [工作台开发](../../工作台开发.md)。本入口按试手原型推进，优先实现核心功能，控制流程与测试规模。

## 8. 设计依据

- [现有内容内核设计](2026-09-30-single-sqlite-content-kernel-design.md)、[项目分层与入口](../../架构分层.md)。
- [Python HTTP server](https://docs.python.org/3/library/http.server.html)：标准库服务器只用于本地开发入口，不作为生产站点服务器。
- [Marked 文档](https://marked.js.org/)：浏览器 Markdown 解析；输出 HTML 需另行净化。
- [DOMPurify](https://github.com/cure53/DOMPurify)：HTML 净化；选择 HTML profile 并保留许可证。
- 固定版本来自 2026-09-30 实际查询的 npm registry：marked 18.0.14（MIT），dompurify 3.4.16（MPL-2.0 OR Apache-2.0）。
