# 维护者部署准备

你的服务器先看 [TEST 起步清单：我要配什么](TEST.md)，朋友看 [6 步操作手册](README.md)。本文由项目维护者处理 GitHub、ACR 和交接文件。

<a id="maintainer"></a>

## 维护者配置：交接给朋友前完成

**先确认部署文件已合并到 main。** 工作流必须已经在 main 上，朋友拿到本地文件不代表 GitHub Actions 已能运行。

**A. ACR 创建两个仓库。** 在已有个人版实例中选好命名空间，创建 `c156-backend`、`c156-frontend`。复制实际公网 registry 域名。

**B. GitHub 使用 Repository 参数。** 到 **Settings → Secrets and variables → Actions**。在 **Variables** 中点 New repository variable，在 **Secrets** 中点 New repository secret。无需创建 GitHub Environment。

| 部署目标 | 用途 | 触发方式 |
| --- | --- | --- |
| `test` | 你的 Ubuntu ECS | 推送 main 自动发布；也可手动选择 test |
| `prod` | 朋友的服务器 | 仅手动 Run workflow 选择 prod |

**按下表的完整名称填写。** TEST_ 对应你的服务器，PROD_ 对应朋友服务器；不要省略前缀。服务器 config.env 仍各自保存自己的域名、端口和目录，数据也分别保留。

| 类型 | test 参数名 | prod 参数名 | 填什么 |
| --- | --- | --- | --- |
| Variable | `TEST_DEPLOY_ENABLED` | `PROD_DEPLOY_ENABLED` | 对应服务器准备就绪后填 `true` |
| Variable | `TEST_ACR_REGISTRY` | `PROD_ACR_REGISTRY` | ACR 公网域名，不带协议或路径 |
| Variable | `TEST_ACR_NAMESPACE` | `PROD_ACR_NAMESPACE` | 已创建的命名空间 |
| Secret | `TEST_DEPLOY_HOST` | `PROD_DEPLOY_HOST` | 对应服务器公网 IP／SSH 域名 |
| Variable（可选） | `TEST_DEPLOY_PORT` | `PROD_DEPLOY_PORT` | 对应 SSH 端口，通常 `22` |
| Variable | `TEST_DEPLOY_USER` | `PROD_DEPLOY_USER` | 在对应服务器执行手册的账号 |
| Variable（可选） | `TEST_DEPLOY_ROOT` | `PROD_DEPLOY_ROOT` | 不填时通过 SSH 读取部署用户家目录并使用其 c156 子目录；自定义时填绝对路径 |
| Secret | `TEST_ACR_USERNAME` | `PROD_ACR_USERNAME` | ACR 登录用户名 |
| Secret | `TEST_ACR_PASSWORD` | `PROD_ACR_PASSWORD` | ACR 登录密码／访问凭据 |
| Secret | `TEST_DEPLOY_SSH_KEY` | `PROD_DEPLOY_SSH_KEY` | 对应服务器专用 SSH 私钥 |

已有 TEST_DEPLOY_ROOT=/home/deploy/c156 可以保留；省略它时默认也是部署用户的家目录/c156。setup.sh 不传目录时也用同一默认值，并自动写入 config.env。自定义路径仍需让 GitHub 与服务器配置一致。

两目标使用同一个 ACR 时，可在两组中分别填相同的 ACR 值；主机、SSH 和数据按服务器分开。两个部署账号需要 Docker 和项目目录操作权限。推荐 NGINX_MANAGED=0，Nginx 校验与重载由管理员或面板负责。

TEST_DEPLOY_ENABLED 未设为 true 时，main 自动发布不会推镜像或更改服务器。手动选 test／prod 时，对应开关未启用会明确报错。选 prod 后如果 PROD_ 参数缺失，会报缺失项，**不会借用 TEST_ 参数**。

**服务器地址也填 Secrets：** TEST_DEPLOY_HOST／PROD_DEPLOY_HOST 不填 Variables，减少 Actions 日志暴露服务器地址。若此前已填在 Variables，先在 Secrets 创建同名项，确认保存成功后再删除 Variable；旧日志不会因此自动消失。

**如果之前已经填过配置：** 在 Repository 新建上表的 TEST_／PROD_ 参数。旧无前缀参数和 Environment 参数不会被新工作流读取；确认其他工作流不需要后，再清理旧项。无需 DEPLOY_ENVIRONMENT 标记。不要重跑仍使用旧方案的历史工作流。

所有凭据由仓库协作者统一管理，TEST_／PROD_ 是配置分组；当前不使用 Environment 审批或访问隔离。prod 的发布入口仍是手动选择。

创建 SSH 部署钥匙、查看已有文件、填写私钥，按 [SSH 钥匙操作步骤](SSH.md)执行。

公钥先加入各自服务器对应账号的 authorized_keys。密码、私钥不要放进仓库或服务器 config.env。

**手动发布怎么选：** Actions → Test, build and deploy → Run workflow，分支 main，选 `target_environment=test` 或 `prod`；这个字段只选择部署目标，不要求创建同名 GitHub Environment。首次拉镜像勾选 `prepare_only`；完成对应服务器初始化和入口配置后，再运行同环境且不勾选 prepare_only。两台的 config.env、images.env 和数据分别保存在各自服务器。

每次运行都会检查、构建和测试本次 main 提交的镜像，通过 artifact 将同一产物交给选定环境推送和部署。prod 手动发布前，核对当前 main 提交已在 test 验证；main 有新提交时，应先验证新版本。镜像包只在该次 workflow 的任务间传递，不包含部署凭据，保留 3 天。

公开 ACR 可以匿名拉取时，服务器无需 docker login，也不用向朋友交付 ACR 推送密码。Actions 推送镜像仍需上述 ACR_USERNAME／ACR_PASSWORD Secrets；这是写权限凭据。

**C. 交接仓库地址和参数。** 无需打压缩包。朋友首次用 HTTPS git clone 到自己的 ~/c156，然后运行 deploy/setup.sh，在根目录填 config.env。公开仓库无需额外的 GitHub Deploy Key。

确认部署代码已在 main，再把仓库地址和操作手册交给朋友。等服务器检查、SSH 公钥和配置完成后运行 prepare；完成初始化和入口配置后正式发布。test 首次安装未准备好时先不要启用自动部署开关；prod 始终手动选择。

首次 clone 用来取得源码、setup 和文档。后续 Actions 通过 SSH 执行 git fetch origin main，检出本次测试镜像对应的准确提交，写入 images.env，再拉镜像并执行下文维护升级顺序。fetch 最多尝试三次，每次 90 秒、间隔 5 秒；服务器必须持续能访问 GitHub；无需 rsync 或源码压缩包。config.env、images.env 和运行目录已加入 Git 忽略。服务器有受 Git 跟踪的本地修改时会停止部署，不强制覆盖。

## 技术说明

数据位置（以 deploy 用户为例；其他用户使用实际家目录）：

```text
/home/deploy/c156/config.env        唯一服务器配置
/home/deploy/c156/data/             整个 SQLite 目录，包含 WAL/SHM
/home/deploy/c156/assets/           资产挂载目录
/home/deploy/c156/backups/          经过校验的快照
/home/deploy/c156/nginx/proxy.inc   项目代理片段
/home/deploy/c156/images.env        Actions 写入的 SHA 和镜像 digest
/home/deploy/c156/deploy/compose.yaml  仓库内的 Compose 配置
```

请求经过宿主机 Nginx（80/443，手动或面板管理）→ 本机 28156 → 前端容器 → FastAPI；后端不公开宿主机端口。默认只构建 amd64，匹配朋友的 x86_64 服务器。提供的 `6.6.47-12.oc9` 内核更接近 OpenCloudOS 9，可用 `cat /etc/os-release` 确认，不照 CentOS 7 的安装教程操作。

GitHub runner 拉官方基础镜像，ECS 拉 ACR 成品镜像。基础镜像固定 digest；阿里云 Docker Hub 加速器存在同步及范围限制，不用它给 runner 保证最新基础镜像。网络受限时，维护者将相同基础镜像同步至可访问 ACR，再改 Dockerfile 引用。[加速器说明](https://help.aliyun.com/zh/acr/user-guide/accelerate-the-pulls-of-docker-official-images)

沿用已有 ACR 个人版，其定位为开发测试、不提供 SLA；需要生产可用性保障时再选择仓库等级。[版本说明](https://help.aliyun.com/zh/acr/product-overview/differences-between-personal-edition-instances-and-enterprise-edition-instances) 为兼容个人版，构建关闭 provenance/SBOM，测试后推同一镜像，部署固定 digest。源码检出到准确 SHA，镜像固定 digest，避免拿到同名标签的其他版本。

备份目前不自动删除，镜像缓存也需按需清理。定期下载备份并检查磁盘空间。数据库跨版本迁移和灾难恢复需要人工安排，不能靠镜像回退恢复旧库。

本地镜像验证（维护者执行）：

```bash
BUILD_SHA=$(git rev-parse HEAD)
docker build --build-arg BUILD_SHA="$BUILD_SHA" -f deploy/backend.Dockerfile -t c156-backend:test .
docker build --build-arg BUILD_SHA="$BUILD_SHA" -f deploy/frontend.Dockerfile -t c156-frontend:test .
BUILD_SHA="$BUILD_SHA" BACKEND_IMAGE=c156-backend:test FRONTEND_IMAGE=c156-frontend:test python3 deploy/smoke.py
```

使用临时库和随机密码，检查 API 登录、读写、重建后持久化和退出。HTTP 检查手动发送 Secure Cookie；浏览器 HTTPS 行为在第 6 步人工确认。

## 简化后的发布行为

不再使用 releases、current、previous 或 prepared。prepare_only 只更新仓库和拉取镜像，不启动网站；普通发布先停写并核实 Compose 已停止，再备份、显式升级、只读核验与 Compose 启动、可选的 Nginx 校验／重载及限时健康轮询（容器健康后最多 90 秒、间隔 5 秒，检查本机与公网前端/API 同 SHA）。失败保留现场，不自动回退或恢复数据库。日志与手动恢复见 [排错清单](TROUBLESHOOTING.md)。同目标的 Actions 串行，服务器额外使用 deploy.lock；早于已成功发布序号的任务会拒绝执行。仓库保持 detached HEAD 是正常的，每次发布会检出准确提交。

Compose 的容器 healthcheck 每 10 秒持续执行；发布脚本的本机／公网轮询只在本次发布中运行，用于确认入口已提供本次 SHA，不启动额外常驻监控。

## P0/P1 维护升级

每次正式发布前，运维必须停止同一数据库的外部 CLI、旧 Web、后台写进程，并保持停止直到维护结束；已有连接和排队写入也必须退出。旧程序兼容标记不能替代停写。脚本不能证明任意外部进程已经停止。

`deploy.sh` 保持原参数和 prepare_only 行为。在已有 deploy.lock 内，正式部署调用 `maintain_and_start`：停止全部 Compose 服务，查询确认没有运行服务，使用候选后端镜像执行源版本可读的 SQLite backup，显式 upgrade，再以只读连接检查 Alembic revision、默认树、外键、integrity_check 和 WAL，全部通过后才 up。备份/迁移/核验失败不会启动服务，也不会自动恢复备份。

启动后或健康检查失败时可能已接受新写入。只修复前进；不得据“发布失败”直接覆盖旧快照。只有人工确认从备份到当前始终未重新开放任何写入口，才可在全部服务停止后显式恢复备份，先保留事故数据目录，再用兼容镜像验证。无法证明停写期间没有新稿时，不执行数据回退。

真实 SQLite 管理命令和维护 helper 的本地检查通过；本地 Compose 边界使用测试替身。随后 GitHub Actions 已完成实际镜像 smoke、容器重建后持久性检查，以及 test 的停写、备份、升级、核验和发布，本机/公网前端及 API 均匹配发布提交；[实际记录](../docs/版本与恢复/执行记录.md)。本地 Docker socket 权限不足的限制不代表远端未验证。prod 未在本轮发布，任意外部写进程停止仍是运维前提。
