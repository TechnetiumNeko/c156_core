# 部署操作手册

适用环境：已有 Docker、Compose、Nginx 和证书面板的 Linux 服务器。朋友提供的 `6.6.47-12.oc9` 内核更接近 OpenCloudOS 9；本文不按 CentOS 7 安装软件，也不要求服务器安装 Python、Node 或 Certbot。可用 `cat /etc/os-release` 确认系统。

公网请求进入面板 Nginx（80/443），再转发到 `127.0.0.1:28156`，最后进入前端容器和 FastAPI。后端没有宿主机公开端口。默认构建 amd64，与提供的 x86_64 服务器一致。

## 项目维护者先准备

1. 在已有 ACR 个人版创建命名空间和两个仓库：`c156-backend`、`c156-frontend`。从控制台复制实际公网仓库域名，不要照抄网上旧版域名。
2. GitHub 仓库 Settings → Secrets and variables → Actions 设置下表；创建名为 `production` 的 Environment，设置服务器参数。首次配置完再开启 `DEPLOY_ENABLED=true`。
3. 把仓库的 `deploy/` 文件夹发给朋友（压缩包即可）。部署工作流还会交付精确提交的 `source.tar.gz`，用于留档和应急，不需要服务器再 `git pull`。

| 位置 | 名称 | 内容 |
| --- | --- | --- |
| Repository variable | `DEPLOY_ENABLED` | `true` 开启镜像推送和部署；缺省只测试和构建 |
| Repository variable | `ACR_REGISTRY` | ACR 公网域名，不带 `https://` 或路径 |
| Repository variable | `ACR_NAMESPACE` | 已有命名空间 |
| Repository secret | `ACR_USERNAME` | ACR 登录用户名 |
| Repository secret | `ACR_PASSWORD` | ACR 登录密码／访问凭据 |
| production variable | `DEPLOY_HOST` | ECS 公网地址或 SSH 域名 |
| production variable | `DEPLOY_PORT` | SSH 端口，缺省 `22` |
| production variable | `DEPLOY_USER` | 已有部署用户，须能操作 Docker、项目目录及执行 Nginx 测试和重载 |
| production variable | `DEPLOY_ROOT` | 缺省 `/srv/c156`，须与服务器配置相同 |
| production secret | `DEPLOY_SSH_KEY` | 该用户专用 SSH 私钥；公钥预先加入服务器 authorized_keys |
| production secret | `DEPLOY_KNOWN_HOSTS` | 经核对的服务器 SSH 主机公钥记录；非 22 端口使用 `[host]:port` 格式 |

流水线不接受未知 SSH 主机。管理员在可信终端核对主机指纹后提供 known_hosts，不能在流水线临时扫描并无条件信任。

GitHub runner 从 Docker 官方仓库构建基础镜像，ECS 从 ACR 拉取成品镜像。阿里云 Docker Hub 加速器当前存在同步及使用范围限制，不应依赖它给 GitHub runner 提供最新基础镜像。镜像已固定官方 digest；若网络受限，维护者将相同基础镜像同步到可访问的 ACR，并更新 Dockerfile 引用。[阿里云加速器说明](https://help.aliyun.com/zh/acr/user-guide/accelerate-the-pulls-of-docker-official-images)

本次沿用已有 ACR 个人版。阿里云将个人版定位为开发测试用途，不提供 SLA；若后续需要生产可用性保障，应再选择合适的仓库等级。[版本说明](https://help.aliyun.com/zh/acr/product-overview/differences-between-personal-edition-instances-and-enterprise-edition-instances)

ACR 个人版兼容性处理：单平台 amd64，关闭构建 provenance/SBOM；推送的就是冒烟检查过的镜像，不重新构建。部署引用不可变 digest。更新基础镜像时需重新执行镜像检查。

## 朋友只需首次做这几步

假设收到的文件夹在 `/tmp/c156-deploy/deploy`，使用有上述权限的现有管理员账号。

**1. 生成配置，再改域名。**

```bash
bash /tmp/c156-deploy/deploy/setup.sh /srv/c156
vi /srv/c156/config.env
bash /tmp/c156-deploy/deploy/setup.sh /srv/c156
```

通常只改 `SITE_DOMAIN`，例如 `docs.example.cn`。若 28156 已占用，改 `APP_PORT`；项目端口只监听本机，无需在安全组放行。`NGINX_BIN` 可填面板 Nginx 的实际路径，例如 `/www/server/nginx/sbin/nginx`。不要自行修改现有 Docker 或 Nginx 安装。

**2. 登录 ACR，并让维护者准备第一次发布。**

```bash
# 域名填写 ACR 控制台提供的公网域名；密码交互输入，不写入命令历史。
docker login YOUR_ACR_REGISTRY
```

维护者在 Actions → Test, build and deploy → Run workflow，选择 main，勾选 `prepare_only`。工作流测试、构建、推送、传文件及拉镜像，最后建立 `/srv/c156/prepared`，此时不会启动未初始化的应用。

**3. 仅新建库时初始化并创建管理员。**

```bash
bash /srv/c156/prepared/setup.sh /srv/c156 --init-db
```

按提示输入管理员密码（不显示）。已有库不运行此命令；脚本也会拒绝覆盖。如果初始化成功但密码步骤被中断，见排错清单，不要删库重试。

**4. 面板建立 HTTPS 站点，配置全站代理。**

中国内地服务器通过域名对外提供网站服务前，需先确认相应备案已完成。[阿里云备案说明](https://help.aliyun.com/zh/icp-filing/basic-icp-service/support/for-the-record-process-faq)

域名 DNS 指向 ECS。安全组和主机防火墙允许该站点 80/443。通过面板申请证书、启用 HTTPS 跳转，并确认自动续签任务和 ACME 验证入口正常。当前只知道面板能配置证书，自动续签尚未确认，首次上线需实际核对。

在面板该站点的 `location /` 内包含下面一行；该 location 使用本项目代理片段，避免同时保留面板生成的另一份 `proxy_pass`：

```nginx
include /srv/c156/nginx/proxy.inc;
```

项目片段只包含 location 内部指令，不包含 server、location 或证书配置。面板若只能管理自己的代理配置，设置 `/srv/c156/config.env` 的 `NGINX_MANAGED=0`，在面板配置全站反代到 `http://127.0.0.1:28156`，同时设置：

```nginx
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Origin $http_origin;
client_max_body_size 2m;
```

保持面板的证书及 ACME location，由面板保存、校验和重载。端口改过时，两处代理端口也要一致。`NGINX_MANAGED=0` 时自动部署不改代理文件或重载 Nginx，仍检查公网 HTTPS。

**5. 让维护者正式发布。**

再次运行工作流，不勾选 `prepare_only`。以后推送 main 自动测试、构建、发布。首次可在浏览器打开 HTTPS 地址，登录、读取和保存一篇文档，确认实际浏览器流程。若站点登录页可用但库中没有文档，这是新空库的正常状态。

朋友后续无需手工拉代码、构建前端或升级容器。部署默认会有短暂中断，不承诺零停机。

## 数据在哪里，怎么备份和回退

```text
/srv/c156/
  config.env              唯一服务器配置，部署不覆盖
  data/c156.sqlite        数据库；同目录保留 WAL/SHM
  assets/                 资产目录（为资产文件保留挂载）
  backups/                每次正式部署前的 SQLite 一致性快照
  releases/<序号>-<重跑次数>-<SHA>/   配置、同版本脚本、source.tar.gz
  nginx/proxy.inc          仅项目代理片段
  current                 当前成功发布
  previous                上一次成功发布
  prepared                已拉取、等待首次初始化的发布
```

不要在运行时单独下载 `data/c156.sqlite` 作为备份；下载 `backups/*.sqlite` 的已完成快照。快照包含账号和文档，备份目录默认只给应用用户访问。资产另行打包下载，不随镜像更新清空。

手动创建备份：

```bash
bash -c 'source /srv/c156/current/common.sh; load_config /srv/c156; compose_for /srv/c156/current exec -T backend python -m src.storage backup --database /data/c156.sqlite --output /backups/manual-$(date -u +%Y%m%dT%H%M%S).sqlite'
```

恢复到上一次成功镜像和代理配置：

```bash
bash /srv/c156/current/rollback.sh /srv/c156
```

回退保留当前数据库和资产，绝不自动用旧备份覆盖用户的新数据。若后续版本引入不兼容数据库变更，应先设计迁移及恢复流程。数据库灾难恢复见 [排错清单](TROUBLESHOOTING.md)。发布、源码和备份目前不自动删除，定期下载并检查磁盘空间。

## 本地验证

```bash
BUILD_SHA=$(git rev-parse HEAD)
docker build --build-arg BUILD_SHA="$BUILD_SHA" -f deploy/backend.Dockerfile -t c156-backend:test .
docker build --build-arg BUILD_SHA="$BUILD_SHA" -f deploy/frontend.Dockerfile -t c156-frontend:test .
BUILD_SHA="$BUILD_SHA" BACKEND_IMAGE=c156-backend:test FRONTEND_IMAGE=c156-frontend:test python3 deploy/smoke.py
```

冒烟检查使用临时库和随机密码，验证最终镜像经过 frontend→backend 的登录、读写、重建后持久化和退出。HTTP 检查手工发送 Secure Cookie，只证明 API 链路；TLS/浏览器行为需在首次上线验证。测试不访问生产库。
