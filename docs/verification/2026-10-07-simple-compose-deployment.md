# 简单 Compose 部署调整

用户确认：项目尚未在服务器运行 setup，改为克隆仓库后在项目目录直接使用 Compose，不再使用 releases 发布目录。

## 最终流程

- 根目录保存 config.env、images.env、data、assets、backups 和 nginx/proxy.inc。
- 首次 git clone；已 clone 旧 main 的用户先安全快进更新，再填写域名，运行 setup。APP_UID/GID 留空时记录部署账号身份；root 账号回退到 10001，避免容器使用 root。
- Actions 保留原来的检查、最终镜像冒烟、同一产物推送 ACR、TEST/PROD 参数和触发方式。
- 通过 SSH 流式执行本次提交的部署脚本；非交互 Compose 备份显式关闭 interactive，Git 与 Compose 更新命令的 stdin 使用 /dev/null，避免读走剩余脚本；服务器锁定部署，git fetch origin main，检出本次镜像对应的 SHA，写 images.env，Compose 拉取及更新。
- 有受跟踪的源码修改时拒绝更新；不执行 reset --hard。早于已成功发布序号的任务会拒绝更新。
- 首次 prepare_only 只拉镜像；之后显式初始化空库和管理员。正式发布保留数据库快照、容器健康、Nginx 和公网 HTTPS 同 SHA 检查。
- 删除 package.sh、rollback.sh、发布软链接与自动回退逻辑。失败停止并保留现场，维护者按排错清单处理。
- setup.sh 和 deploy.sh 增加执行位；文档仍统一使用 bash 执行。

## 本地验证

在 .worktrees/compose-deploy、feat/simple-compose 中运行：

- `.worktrees/vue-fastapi-loop/.venv/bin/python -m unittest tests.test_deployment tests.test_backup tests.test_server_production -q`（使用该 Python 的绝对路径）：13 个相关用例通过。
- 新部署用例执行真实脚本与临时 Git 上游，验证准确 SHA 检出（上游已有更新提交）、本地修改保留、旧序号拒绝及配置保留；未替换系统工具。
- `actionlint -shellcheck /tmp/c156-check-tools/shellcheck-v0.11.0/shellcheck`：通过。
- `shellcheck -x -P deploy deploy/*.sh` 与逐文件 `bash -n`：通过。
- 使用真实 Docker Compose 2.40.3 解析 compose_for 输出，核对 digest、UID/GID、挂载路径及 127.0.0.1:28156：通过，无需启动 Docker daemon。
- 部署文档本地链接与 26 个 Bash 代码块语法：通过。
- `git diff --check`：通过。

本次未重新构建镜像或启动容器，Dockerfile、Compose 服务和应用代码均未修改；新部署流程尚未在真实 ECS 执行。既有镜像测试仍由 CI 在每次发布执行。
