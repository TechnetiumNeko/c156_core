# 维护者部署准备

朋友只需看 [6 步操作手册](README.md)。本文由项目维护者处理 GitHub、ACR 和交接文件。

<a id="maintainer"></a>

## 维护者配置：交接给朋友前完成

**先确认部署文件已合并到 main。** 工作流必须已经在 main 上，朋友拿到本地文件不代表 GitHub Actions 已能运行。

**A. ACR 创建两个仓库。** 在已有个人版实例中选好命名空间，创建 `c156-backend`、`c156-frontend`。复制实际公网 registry 域名。

**B. GitHub 建立两个独立 Environment。** 到 **Settings → Environments**，分别创建 `test` 和 `prod`。

| Environment | 用途 | 触发方式 |
| --- | --- | --- |
| `test` | 你的 Ubuntu ECS | 推送 main 自动发布；也可手动选择 test |
| `prod` | 朋友的服务器 | 仅手动 Run workflow 选择 prod |

**在每个 Environment 内分别填完整的 Variables 和 Secrets。** 参数名相同，值属于对应服务器。服务器的 config.env 也各自保存自己的域名、端口和目录，不共享数据库或资产。

| 类型 | 名称 | test 填什么 | prod 填什么 |
| --- | --- | --- | --- |
| Variable | `DEPLOY_ENVIRONMENT` | `test` | `prod` |
| Variable | `DEPLOY_ENABLED` | 准备就绪后 `true` | 准备就绪后 `true` |
| Variable | `ACR_REGISTRY` | 本环境 ACR 公网域名，不带协议或路径 | 本环境 ACR 公网域名 |
| Variable | `ACR_NAMESPACE` | 本环境已创建的命名空间 | 本环境已创建的命名空间 |
| Variable | `DEPLOY_HOST` | 你的 ECS 公网 IP／SSH 域名 | 朋友服务器公网 IP／SSH 域名 |
| Variable | `DEPLOY_PORT` | 你的 SSH 端口，通常 `22` | 朋友的 SSH 端口 |
| Variable | `DEPLOY_USER` | 你的部署账号 | 朋友执行手册的账号 |
| Variable | `DEPLOY_ROOT` | `/srv/c156` | `/srv/c156` |
| Secret | `ACR_USERNAME` | 本环境 ACR 登录用户名 | 本环境 ACR 登录用户名 |
| Secret | `ACR_PASSWORD` | 本环境 ACR 登录密码／访问凭据 | 本环境 ACR 登录密码／访问凭据 |
| Secret | `DEPLOY_SSH_KEY` | 你的服务器专用 SSH 私钥 | 朋友服务器专用 SSH 私钥 |
| Secret | `DEPLOY_KNOWN_HOSTS` | 核对指纹后的你的主机记录 | 核对指纹后的朋友主机记录 |

两环境使用同一个 ACR 时，可以分别填相同的 ACR 值；SSH、主机、域名和数据始终按服务器分开。两个部署账号都需要 Docker、项目目录以及对应 Nginx 操作权限。

`DEPLOY_ENVIRONMENT` 必须与选定环境匹配，防止旧配置被误用于新目标。`DEPLOY_ENABLED` 未设为 true 时，main 的自动 test 发布不推镜像、不改服务器；手动发布会明确报“环境未启用”。

**迁移旧配置：** 将原来 Repository 级的上述 Variables／Secrets 移到对应 Environment，并删除 Repository 级同名项；GitHub 会继承上层同名值，不能只复制新配置而保留旧值。旧 `production` Environment 不再被当前工作流使用，确认没有其他工作流依赖后清理；不要重跑仍引用旧配置的历史工作流。

SSH 指纹必须在可信终端核对，不在流水线临时扫描后直接信任。非 22 端口的 known_hosts 用 `[host]:port` 格式。公钥先加入各自服务器对应账号的 authorized_keys。密码、私钥不要放进仓库或服务器 config.env。

**手动发布怎么选：** Actions → Test, build and deploy → Run workflow，分支 main，选 `target_environment=test` 或 `prod`。首次拉镜像勾选 `prepare_only`；完成对应服务器初始化和面板配置后，再运行同环境且不勾选 prepare_only。两台的 prepared/current、发布序号和回退记录分别保存在各自服务器。

每次运行都会检查、构建和测试本次 main 提交的镜像，通过 artifact 将同一产物交给选定环境推送和部署。prod 手动发布前，核对当前 main 提交已在 test 验证；main 有新提交时，应先验证新版本。镜像包只在该次 workflow 的任务间传递，不包含部署凭据，保留 3 天。

**C. 打包交接文件。** 在包含本次部署代码的本地仓库运行：

```bash
git archive --format=zip --output=c156-deploy.zip HEAD deploy
```

交付压缩包及开头的信息表。等朋友完成第 3 步的 Docker 登录后再运行 prepare；完成第 5 步的面板配置后再正式部署。test 首次安装期间避免额外推送 main，因为它会触发 test 的正式部署。main 推送不会发布到 prod。


## 技术说明

数据位置：

```text
/srv/c156/config.env        唯一服务器配置
/srv/c156/data/             整个 SQLite 目录，包含 WAL/SHM
/srv/c156/assets/           资产挂载目录
/srv/c156/backups/          经过校验的快照
/srv/c156/nginx/proxy.inc   项目代理片段
/srv/c156/releases/         每次发布文件和同版本源码
/srv/c156/current           当前成功发布
/srv/c156/previous          上一次成功发布
/srv/c156/prepared          首次准备好的发布
```

请求经过面板 Nginx（80/443）→ 本机 28156 → 前端容器 → FastAPI；后端不公开宿主机端口。默认只构建 amd64，匹配朋友的 x86_64 服务器。提供的 `6.6.47-12.oc9` 内核更接近 OpenCloudOS 9，可用 `cat /etc/os-release` 确认，不照 CentOS 7 的安装教程操作。

GitHub runner 拉官方基础镜像，ECS 拉 ACR 成品镜像。基础镜像固定 digest；阿里云 Docker Hub 加速器存在同步及范围限制，不用它给 runner 保证最新基础镜像。网络受限时，维护者将相同基础镜像同步至可访问 ACR，再改 Dockerfile 引用。[加速器说明](https://help.aliyun.com/zh/acr/user-guide/accelerate-the-pulls-of-docker-official-images)

沿用已有 ACR 个人版，其定位为开发测试、不提供 SLA；需要生产可用性保障时再选择仓库等级。[版本说明](https://help.aliyun.com/zh/acr/product-overview/differences-between-personal-edition-instances-and-enterprise-edition-instances) 为兼容个人版，构建关闭 provenance/SBOM，测试后推同一镜像，部署固定 digest。发布目录名包含序号、重跑次数和 SHA，成功发布文件不会被重跑覆盖。

发布、源码、备份目前不自动删除。定期下载备份并检查磁盘空间。数据库跨版本迁移和灾难恢复需要人工安排，不能靠镜像回退恢复旧库。

本地镜像验证（维护者执行）：

```bash
BUILD_SHA=$(git rev-parse HEAD)
docker build --build-arg BUILD_SHA="$BUILD_SHA" -f deploy/backend.Dockerfile -t c156-backend:test .
docker build --build-arg BUILD_SHA="$BUILD_SHA" -f deploy/frontend.Dockerfile -t c156-frontend:test .
BUILD_SHA="$BUILD_SHA" BACKEND_IMAGE=c156-backend:test FRONTEND_IMAGE=c156-frontend:test python3 deploy/smoke.py
```

使用临时库和随机密码，检查 API 登录、读写、重建后持久化和退出。HTTP 检查手动发送 Secure Cookie；浏览器 HTTPS 行为在第 6 步人工确认。
