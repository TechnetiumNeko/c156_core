# C156 Core

用于共同创作和阅读内容的网站原型。当前实现统一 SQLite 内容内核，CLI 和 HTML／JS 工作台通过同一个 `ContentService` 读写虚拟目录和文档。工作台用于试手和演示架构，采用原生前端与 Python 标准库，无需安装运行依赖或执行前端构建。

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

账号系统将身份、凭据、会话、成员关系和权限判断作为独立边界：Web 负责 Cookie、CSRF 和登录页面，服务层负责认证与授权，内容服务只接收已经解析的调用者身份。实时协作将更新模型、版本向量和合并规则与房间、同步、Presence、WebSocket 传输分开；稳定正文检查点仍由内容和版本服务保存。当前这些账号、协作和发布模块尚未实现，本文档记录的是后续扩展方向。

## 快速试用

在仓库根目录运行（已验证 Python 3.13.2）：

```bash
python run_demo.py
# Linux／macOS 也可运行：./start_demo.sh
```

首次自动创建独立的 `.c156/demo.sqlite`，加入示例文档并打开浏览器，默认地址为 <http://127.0.0.1:8000/>。再次启动保留已保存的修改；重复启动会打开已有工作台，端口被其他程序占用时显示实际分配的地址。

工作台支持目录浏览、新建目录／文档、Markdown 源码／预览／并排查看、保存及 Ctrl／Cmd+S。输入不会自动保存，未保存的草稿仅在当前页面内存中；切换和关闭会提醒，保存冲突时保留草稿供手动合并。

服务器终端可使用 `python run_demo.py --no-browser`，通过 SSH 转发端口后访问；用 `--port` 指定端口，按 Ctrl+C 停止。服务仅监听 `127.0.0.1`。CLI 可访问同一示例库：

```bash
python run_cli.py --database .c156/demo.sqlite
```

## 使用自己的内容库

先停止演示服务，或为 Web 指定不同端口。导入仓库保留的旧样本（7 个文件夹、1 篇文档），再打开工作台：

```bash
python -m src.storage migrate-legacy --source data --database data/c156.sqlite
python run_web.py --database data/c156.sqlite --port 8000
# 等价入口：python -m src.web --database data/c156.sqlite --port 8000
# 终端入口：python run_cli.py --database data/c156.sqlite
```

创建空的新库使用 `python -m src.storage init --database /path/to/new.sqlite`，然后把同一路径传给 CLI 或 Web。原始样本保留不变，迁移不覆盖不匹配的目标。CLI／Web 的默认库为 `data/c156.sqlite`，启动只校验数据库，不自动创建、导入或修复；演示入口的初始化流程与它们分开。所有入口的 `--help` 均不创建数据库。

## 当前范围

内核已支持稳定对象 ID、虚拟目录、正文修订、元数据、创建、重命名、移动、软删除、事务与版本冲突检查。CLI 保留原有命令，Web 提供浏览、新建与正文读写。虚拟 `/` 是 `main` 文件夹；`admin`、`resource`、`bin` 为兼容目录，软删除不自动移入 `bin`。

用户认证与权限、实时协作、作品级提交与分支、历史恢复、媒体上传和公开站点部署仍是后续阶段。后续实现应将这些能力放在共用应用服务层，再由 Web 和 CLI 接入。正文修订不等于作品级提交；访问根限制不等于用户授权。

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
| 身份、授权与实时协作的边界 | [架构分层](docs/架构分层.md)、[协作与版本设计](docs/协作与版本设计.md) |
| 内核及 HTTP 的详细契约 | [内核设计](docs/superpowers/specs/2026-09-30-single-sqlite-content-kernel-design.md)、[工作台设计](docs/superpowers/specs/2026-09-30-web-document-workbench-design.md) |

`src/core` 放纯数据与规则，`src/services` 放业务操作，`src/storage` 放存储和管理命令；`src/cli`、`src/editor`、`src/web` 分别处理入口交互。`data` 保留旧迁移样本，运行库、缓存和本机启动快捷方式不纳入 Git。`docs/superpowers/plans` 保留已完成的实施记录。

验证使用临时数据库，不修改示例库或原始样本：

```bash
python -m unittest discover -s tests -v
node --experimental-default-type=module --test tests/web/*.test.mjs
```

前端验证使用 Node 22，实际 HTML 净化检查需额外的 jsdom；配置方法见 [工作台开发](docs/工作台开发.md#运行与验证)。工作台运行本身无需 Node。浏览器验证默认只打开并截图。
