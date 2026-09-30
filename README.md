这是 C156 Project 的网站原型代码仓库，使用 Python 和标准库 SQLite 开发。

统一内容内核已接入 CLI 和终端编辑器：运行内容保存在一个 SQLite 数据库中，入口通过 `ContentService` 读写虚拟目录与文档。网页入口仍待实现。协议与验收约定见[设计方案](docs/superpowers/specs/2026-09-30-single-sqlite-content-kernel-design.md)及[实施计划](docs/superpowers/plans/2026-09-30-single-sqlite-content-kernel.md)；最终集成审阅仍待完成。

首次使用请选择相应的管理命令，再启动 CLI：

```bash
# 导入仓库的旧样本（7 个文件夹、1 篇文档），保留来源：
python -m src.storage migrate-legacy --source data --database data/c156.sqlite
# 或创建空的新库（不要用此命令替代旧数据导入）：
python -m src.storage init --database /path/to/new.sqlite
python run_cli.py --database data/c156.sqlite
# 同样可用：python -m src.cli --database data/c156.sqlite
```

CLI 默认库路径为 `data/c156.sqlite`，不会自动建库、导入或修复。管理命令显式初始化／配置 WAL；运行打开只验证协议版本 1、默认树和 WAL 就绪状态。重复迁移验证相同来源后恢复运行配置，保留导入后的编辑；已有不匹配目标不会被覆盖。已弃用的 `python -m src.file.bootstrap --database PATH` 仅转发显式 init，不重扫旧目录。各入口的 `--help` 不创建数据库。

CLI 虚拟 `/` 是 `main` 文件夹；初始化返回的工作区根还包含 `admin`、`resource` 和 `bin`，两种 scope 不相同。`root_id` 限制活动子树范围，不代表用户授权。内核支持创建、正文修订、元数据、重命名、移动与软删除；CLI 保留现有命令集合。正文修订为未来恢复提供基础，目前不提供用户历史浏览／恢复、分支创建／切换、作品提交图、权限或 CRDT 协作。`bin` 只是兼容顶层目录，软删除不会自动搬入其中。

- `data`：保留的旧协议迁移样本；运行库、WAL／SHM／journal 和迁移临时文件不纳入 Git。
- `docs`：开发文档。
- `src`：入口、服务、纯核心模型和存储实现。
- `tests`：临时数据库及样本副本上的标准库测试。

验证：`python -m unittest discover -s tests -v`。详见[项目分层与入口](docs/架构分层.md)、[文件格式](docs/文件格式.md)、[文件系统](docs/文件系统.md)及[用户界面](docs/用户界面.md)。
