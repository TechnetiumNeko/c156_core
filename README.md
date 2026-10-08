# C156 Core

用于共同创作和阅读内容的网站原型。当前实现统一 SQLite 内容内核，CLI、新 Vue 工作台和旧 HTML／JS 工作台通过同一个 `ContentService` 读写虚拟目录和文档。新工作台采用 Vue + FastAPI，提供登录、账号与成员管理、文件操作、Markdown 实时预览编辑、手动保存和冲突处理；旧原生前端与 Python 标准库 HTTP 入口继续保留。

现有协议 2 数据库须在停写备份后显式执行 `python -m src.storage upgrade --database PATH`；普通启动只验证版本。[行为与验证范围](docs/版本与恢复/行为规范.md)。

## 架构边界

项目按“领域内核 → 存储 → 应用服务 → 入口适配器”组织。`core` 只放纯数据和规则，`storage` 负责持久化，`services` 负责跨入口共用的业务操作；CLI、HTTP 和未来的 WebSocket 只是不同的适配器。账号和实时协作属于跨入口的应用能力，不直接放进网页代码，也不把 Web 请求细节带入内容内核。

```text
浏览器 HTTP／WebSocket       CLI
            \                /
             应用服务层
    身份认证／授权／内容／协作／发布
                    |
             领域与数据内核
          core 规则 + storage 仓储
```

账号系统将身份、凭据、会话、成员关系和权限判断作为独立边界：Web 负责 Cookie、CSRF 和登录页面，服务层负责认证与授权，内容服务接收明确的 session_token，在同一事务内解析身份与授权。实时协作将更新模型、版本向量和合并规则与房间、同步、Presence、WebSocket 传输分开；稳定正文检查点仍由内容和版本服务保存。身份认证、账号管理、成员关系与授权已实现；实时协作和作品发布仍未交付。

## Vue + FastAPI 本地开发

使用 Python 3.13 和 Node 22，在仓库根目录安装隔离依赖：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
npm --prefix frontend ci
```

首次使用先显式准备独立库。下面路径仅供本机试用，不写默认库或原始样本；密码由终端隐藏输入并确认：

```bash
.venv/bin/python -m src.storage init --database /tmp/c156-vue-local.sqlite
.venv/bin/python -m src.identity bootstrap-admin --database /tmp/c156-vue-local.sqlite --login-name owner --display-name 管理员
```

两个终端分别启动（需要已有受支持 Alembic revision 的 WAL 库；普通启动不初始化、播种、升级或修复）：

```bash
.venv/bin/python run_server.py --database /tmp/c156-vue-local.sqlite --port 8001
# 另一终端
npm --prefix frontend run dev
```

打开 <http://127.0.0.1:5173/>；Vite 将 `/api` 代理至 loopback 后端 8001，保留 Host/Origin。后端为单 worker，无 CORS，不信任转发来源头。端口占用时先停止自己启动的进程或显式配置其他端口，配置方法见 [工作台开发](docs/工作台开发.md)。结束时分别 Ctrl+C。

新页面使用 Naive UI + UnoCSS，正文采用 CodeMirror 实时预览：默认显示格式，光标进入相应范围时显露 Markdown 标记；完整源码和独立阅读是次级入口。目录右键或操作按钮提供新建、重命名和删除；顶栏提供个人账号、站点账号和默认工作区管理。高级对象 ACL、所有权转移、LaTeX 与可视化表格编辑尚未接入新页面，可按需要使用旧入口或 CLI 的已有管理能力。草稿通过原生 IndexedDB 保存，Web Locks 保证同一草稿单页编辑；不支持所需能力时只读。历史区独立查看和比较，恢复前须处理未保存稿，保存和恢复用固定操作键查询或重试。退出保留已存本机稿；存储失败时先复制、重试或明确确认风险后退出。默认库仍为 `data/c156.sqlite`，只有明确的管理命令会创建或导入它。

## 旧工作台快速试用

在仓库根目录运行（已验证 Python 3.13.2）：

```bash
python -m pip install -r requirements.txt
python run_demo.py
# Linux／macOS 也可运行：./start_demo.sh
```

首次在新路径显式初始化独立的 `.c156/demo.sqlite`，提示首管理员登录名、显示名和两次隐藏密码（8–128 个字符），创建站点管理员兼默认工作区 owner。演示通过正常登录取得会话、经授权播种示例后退出；浏览器需要重新登录，不提供默认密码。默认地址为 <http://127.0.0.1:8000/>。再次启动只打开已有库，保留修改且不重新播种。已有库未引导时要求先执行 bootstrap-admin；协议 1 运行库必须改用新路径，不升级或覆盖。重复启动用不含原始 ID 的实例标识确认同一库，端口被其他程序或其他库占用时显示实际分配的地址。

工作台支持目录浏览、新建目录／文档、Markdown 源码／预览／并排查看、保存及 Ctrl／Cmd+S。输入不会自动保存，未保存的草稿仅在当前页面内存中；切换和关闭会提醒，保存冲突时保留草稿供手动合并。

服务器终端可使用 `python run_demo.py --no-browser`，通过 SSH 转发端口后访问；用 `--port` 指定端口，按 Ctrl+C 停止。服务仅监听 `127.0.0.1`。CLI 可访问同一示例库：

```bash
python run_cli.py --database .c156/demo.sqlite
```

## 使用自己的内容库

先停止演示服务，或为 Web 指定不同端口。导入仓库保留的旧样本（7 个文件夹、1 篇文档），再打开工作台：

```bash
python -m src.storage migrate-legacy --source data --database data/c156.sqlite
python -m src.identity bootstrap-admin --database data/c156.sqlite --login-name owner --display-name 管理员
python run_web.py --database data/c156.sqlite --port 8000
# 等价入口：python -m src.web --database data/c156.sqlite --port 8000
# 终端入口：python run_cli.py --database data/c156.sqlite
```

创建空的新库使用 `python -m src.storage init --database /path/to/new.sqlite`，再执行同路径的 `python -m src.identity bootstrap-admin --database /path/to/new.sqlite --login-name owner --display-name 管理员`，然后启动 CLI 或 Web 并正常登录。init 和旧容器导入通过固定 Alembic 迁移链创建内容库，不自动创建账号；bootstrap 只允许空账号且无 owner 的库，密码通过 getpass 输入并确认，不放在命令行。原始样本保留不变，迁移不覆盖不匹配的目标。CLI／Web 的默认库为 `data/c156.sqlite`，启动只校验数据库，不自动创建、导入或修复；演示入口的初始化流程与它们分开。所有入口的 `--help` 均不创建数据库。

## 当前范围

内核已支持稳定对象 ID、虚拟目录、正文修订、元数据、创建、重命名、移动、软删除、事务与版本冲突检查。CLI 保留原有命令，Web 提供浏览、新建与正文读写。虚拟 `/` 是 `main` 文件夹；`admin`、`resource`、`bin` 为兼容目录，软删除不自动移入 `bin`。

账号、密码、24 小时会话、站点账号管理、默认工作区成员／角色、继承 ACL、私密与文档冻结已实现。文档历史浏览、正文恢复、操作回执和本机草稿已接入 Vue；已删除历史仅向工作区 admin/owner 开放，不提供恢复删除。实时协作、作品级提交与分支、媒体上传仍属后续阶段。生产部署脚本已提供显式迁移流程，真实部署验收见部署文档。后续实现应将这些能力放在共用应用服务层，再由 Web 和 CLI 接入。正文 revision_id 检测正文冲突；entry.version 检测结构和 metadata，workspace_access_settings.version 检测成员／ACL／阅读范围／已有对象私密／冻结配置。工作区 owner 不等于 private 创建者；站点管理员也不会自动获得其他工作区内容权限。默认 read_scope 为 members；可配置 authenticated／everyone 的阅读基线，但所有写操作仍要求有效成员。reader 默认读取，editor 默认读取／编辑／创建／改名／移动／删除，admin／owner 管理工作区授权与内容，owner 可转移所有权；review／publish 仅建模配置，没有发布流程。ACL 按最近对象及 user、role、authenticated、everyone 顺序计算，无法阅读的祖先不会因私密所有权而被跳过。其他人的冻结不会被管理角色静默绕过。

站点管理员创建账号后一次性取得 48 小时激活凭据；重置凭据有效 1 小时，重置和改密会撤销旧会话。凭据应私下交给对应用户，不写日志。CLI 支持 login／logout 和 mkdir／edit --private；网页提供账号与访问管理。草稿绑定原用户：会话失效暂停保存并保留正文和原基础修订，同用户重登继续，换账号须处理旧草稿；草稿仅驻留内存。

## 开发与文档

第一次接手代码，从 [代码导览](docs/代码导览.md) 开始：先读哪些文件、核心数据结构、每个文件的职责、保存链路，以及按功能查找实现和测试。合并行数中约一半是测试，旧数据迁移可在需要导入旧库时再读。

| 内容 | 文档 |
| --- | --- |
| 阅读顺序、文件职责与修改定位 | [代码导览](docs/代码导览.md) |
| 分层职责与调用关系 | [架构分层](docs/架构分层.md) |
| 前端模块、按钮与 API 接线 | [工作台开发](docs/工作台开发.md) |
| CLI、终端编辑器与浏览器操作 | [用户界面](docs/用户界面.md) |
| 虚拟目录、路径与访问范围 | [文件系统](docs/文件系统.md) |
| 数据库协议与旧数据导入 | [文件格式](docs/文件格式.md) |
| 产品目标与后续协作设计 | [需求整理](docs/requirements.md)、[协作与版本设计](docs/协作与版本设计.md) |
| 总体开发顺序与 P0/P1 准备 | [架构与开发路线图](docs/架构与开发路线图.md)、[版本与恢复](docs/版本与恢复/README.md) |
| 身份、授权与实时协作的边界 | [架构分层](docs/架构分层.md)、[协作与版本设计](docs/协作与版本设计.md) |
| 内核及 HTTP 的详细契约 | [内核设计](docs/superpowers/specs/2026-09-30-single-sqlite-content-kernel-design.md)、[工作台设计](docs/superpowers/specs/2026-09-30-web-document-workbench-design.md) |

`src/core` 放纯数据与规则，`src/services` 放业务操作，`src/storage` 放存储和管理命令；`src/cli`、`src/editor`、`src/web` 分别处理入口交互。`data` 保留旧迁移样本，运行库、缓存和本机启动快捷方式不纳入 Git。`docs/superpowers/plans` 保留已完成的实施记录。

验证使用临时数据库，不修改示例库或原始样本：

```bash
python -m unittest discover -s tests -v
node --experimental-default-type=module --test tests/web/*.test.mjs
```

前端验证使用 Node 22，实际 HTML 净化检查需额外的 jsdom；配置方法见 [工作台开发](docs/工作台开发.md#运行与验证)。旧工作台运行本身无需 Node；新工作台开发需要 Vite。新工程检查为 `npm --prefix frontend run test`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`。浏览器验证默认只打开并截图。

Docker 与自动部署见 [部署操作手册](deploy/README.md)，常见问题见 [排错清单](deploy/TROUBLESHOOTING.md)。
