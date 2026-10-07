# 部署操作手册

维护者先看 [GitHub 参数与发布入口](MAINTAINER.md)，自己的 test 服务器可看 [TEST 清单](TEST.md)。朋友从本文第 1 步开始。

第一次部署按下面 **1 → 6** 做。每步看到“正常结果”再继续；报错就点该步的排错链接。以后更新由 GitHub Actions 完成。

默认目录是部署用户的 `$HOME/c156`（deploy 用户为 `/home/deploy/c156`，root 用户为 `/root/c156`），默认端口 `28156`。文中的面板路径以 deploy 用户为例，其他用户按 setup 输出的实际路径填写。服务器需要 Docker 和 Compose 插件。网站入口二选一：Ubuntu 手动 Nginx + Certbot，或已有服务器面板。你的 Ubuntu ECS 使用 `test`，朋友服务器使用 `prod`；GitHub 参数放在 Repository，分别以 TEST_／PROD_ 开头；两台执行同一套应用部署步骤，无需在宿主机安装 Python 或 Node。Ubuntu 手动入口由管理员安装 Nginx 和 Certbot；已有面板沿用面板的证书管理。

## 开始前：维护者把东西准备好

维护者先完成[维护者配置](MAINTAINER.md#maintainer)，再把以下内容交给朋友：

| 交给朋友 | 填什么 |
| --- | --- |
| 仓库地址 | `https://github.com/TechnetiumNeko/c156_core.git`，维护者先确认部署代码已在 main |
| 部署环境 | 你的服务器填 `test`，朋友服务器填 `prod`；后面两次 Run workflow 都选这个环境 |
| 网站域名 | 一个确定的域名，例如 `docs.example.cn` |
| ACR 公网域名 | 从控制台复制，不带 `https://` 或仓库路径 |
| ACR 拉取凭据（仅私有仓库） | 公开仓库可匿名拉取时无需交付；私有仓库按需提供拉取凭据 |
| 服务器登录账号 | 与对应 TEST_DEPLOY_USER／PROD_DEPLOY_USER 相同，能操作 Docker 和项目目录；Nginx 由管理员或面板管理 |
| 联系人 | 遇到 Actions 或 SSH 报错时找谁 |

中国内地服务器上线前先确认域名备案已完成。[阿里云说明](https://help.aliyun.com/zh/icp-filing/basic-icp-service/support/for-the-record-process-faq)

## 1. 朋友：git clone，检查服务器

用上表的服务器账号登录。首次直接克隆到自己的 c156 目录：

开始前需要 Git、curl、flock、tar、ss、timeout。Ubuntu 缺这些命令时由管理员运行 `sudo apt-get update`、`sudo apt-get install -y git curl util-linux coreutils iproute2`。Docker 与 Compose 未安装时按 [Docker 官方 Ubuntu 步骤](https://docs.docker.com/engine/install/ubuntu/) 安装 Docker Engine 和 Compose 插件，再让部署账号确认 `docker info` 可用。

**在服务器终端逐行运行：**

```bash
# 网络操作最多 3 次，每次最多 90 秒；已有目录不要重复 clone。
git_retry() {
  for attempt in 1 2 3; do
    echo "Git attempt $attempt/3"
    if GIT_TERMINAL_PROMPT=0 timeout --kill-after=5s 90s git "$@" </dev/null; then return 0; fi
    if [ "$attempt" -eq 3 ]; then return 1; fi
    sleep 5
  done
}
git_retry clone https://github.com/TechnetiumNeko/c156_core.git "$HOME/c156"
cd "$HOME/c156"
ls deploy/setup.sh
docker info >/dev/null && echo 'Docker OK'
docker compose version
nginx -v
command -v git curl flock tar ss timeout
```

**正常结果：** 仓库克隆成功，显示 deploy/setup.sh 路径；随后有 `Docker OK`、Compose 版本；已有 Nginx 且在 PATH 中时显示其版本。最后显示六个命令的路径。Nginx 暂未安装或不在 PATH 中时，按第 5 步选择入口完成安装或面板配置即可。

**你已经 clone 过旧版本：** 不要重复 clone，先确认维护者交付的版本已合并 main，先复制上面的 `git_retry()` 函数定义（不运行 clone），再运行下面这段取得新版 setup。看到成功更新后继续第 2 步；源码有修改或无法快进时按 [E01](TROUBLESHOOTING.md#e01) 处理。

```bash
cd "$HOME/c156"
if git diff --quiet && git diff --cached --quiet; then
  git_retry fetch origin main && git merge --ff-only origin/main
else
  echo '源码有本地修改，先交给维护者处理；不要强制覆盖。'
fi
```

如果目录含其他项目或现有数据，不要删除或覆盖，按 [E01](TROUBLESHOOTING.md#e01) 确认。公开仓库用 HTTPS 克隆，无需另配 GitHub Deploy Key。

Ubuntu 手动入口可在第 5 步安装 Nginx。面板入口如果 `nginx -v` 找不到命令，但面板的 Nginx 正常运行，推荐模式 NGINX_MANAGED=0 不需要在 PATH 中寻找 Nginx。其他命令缺失或权限报错，先解决再继续。

**失败定位：** [E01 文件位置不对](TROUBLESHOOTING.md#e01)；[E02 组件缺失或权限不足](TROUBLESHOOTING.md#e02)。

## 2. 朋友：生成配置，填写域名和入口管理方式

**复制配置（已有 config.env 时保留）：**

```bash
cd "$HOME/c156"
if [ ! -f config.env ]; then
  install -m 600 deploy/config.env.example config.env
fi
```

用编辑器或面板打开项目根目录的 config.env（deploy 用户默认 `/home/deploy/c156/config.env`），把这一行改成真实域名：

```dotenv
SITE_DOMAIN=docs.example.cn
```

DEPLOY_ROOT 留空即可，setup 会使用当前用户家目录/c156 并写入实际路径；无需手工改目录。域名这里只填：**不加 `https://`，不加 `/`，不加引号或空格**。其他设置通常不用改。

推荐两条入口都填写：

```dotenv
NGINX_MANAGED=0
```

Nginx 和证书由管理员或面板配置、重载；Actions 只更新容器，并校验公网 HTTPS，不要求 deploy 用户有 Nginx 管理权限。`NGINX_BIN` 在这个模式下无需修改。已有的项目 include 接法仍可使用，见第 5 步。

保存后运行：

```bash
bash "$HOME/c156/deploy/setup.sh"
```

**正常结果：** 最后一行包含 `Directories ready`，并列出 data、assets、backups 的路径。

APP_UID 和 APP_GID 留空时，setup 会自动填写部署账号的 UID/GID，容器使用同一身份访问数据，通常无需 chown。若你已复制旧配置中的 10001，把这两项清空后再运行 setup。明确使用自定义 UID/GID 时需提前安排目录权限，见 [E02](TROUBLESHOOTING.md#e02)。

**失败定位：** [E03 配置、端口或网络](TROUBLESHOOTING.md#e03)；[E02 权限或组件](TROUBLESHOOTING.md#e02)；[E08 Nginx 路径](TROUBLESHOOTING.md#e08)。

## 3. 准备镜像访问，维护者准备发布

**你的 ACR 是公开仓库：** 可以匿名拉取时跳过服务器 docker login，直接通知维护者准备镜像。GitHub Actions 推送仍需要维护者填写 ACR Secrets。

**仅私有仓库或要求登录的仓库：** 朋友在服务器运行，将下面 `YOUR_ACR_REGISTRY` 换成维护者给的 ACR 公网域名。

```bash
docker login YOUR_ACR_REGISTRY
```

按提示输入 ACR 用户名和密码。密码不要写进命令。

**需要登录时的正常结果：** `Login Succeeded`。然后告诉维护者：“服务器已准备好，可以准备镜像了。”

**运行前确认 SSH 配置完成：** Actions 登录服务器所用钥匙 见 [SSH 操作步骤](SSH.md)。

**维护者在 GitHub 操作：**

1. 仓库 → **Actions** → **Test, build and deploy** → **Run workflow**。
2. 分支选 **main**；`target_environment` 选本台服务器的 **test** 或 **prod**；勾选 **prepare_only**，点 **Run workflow**。
3. 等 `checks`、`images`、`deploy` 都变绿，再通知朋友继续。

**朋友确认：**

```bash
test -s "$HOME/c156/images.env" && echo 'Images ready'
```

**正常结果：** 显示 `Images ready`。这一步只准备文件和镜像，网站尚未启动。

**失败定位：** [E04 ACR 登录或拉镜像失败](TROUBLESHOOTING.md#e04)；[E05 SSH 失败](TROUBLESHOOTING.md#e05)；[E06 Actions 或 images.env 缺失](TROUBLESHOOTING.md#e06)。

## 4. 朋友：创建新库和管理员

**仅第一次创建空库时运行：**

```bash
bash "$HOME/c156/deploy/setup.sh" "$HOME/c156" --init-db
```

提前准备 **15～128 个字符**的网站管理员密码。看到 `Password:` 后输入密码；看到 `Confirm password:` 再输一次。输入时屏幕不显示字符，这是正常的。网站账号固定为 **admin**，密码是这里设置的，与 ACR 密码无关。

**正常结果：** 命令成功结束，最后包含 `Directories ready`。之后不要再运行 `--init-db`。

如果已经有数据库，或曾经运行到一半，不要删库重来，按下面的排错项处理。

**失败定位：** [E07 初始化中断、已有库或管理员失败](TROUBLESHOOTING.md#e07)。

## 5. 朋友：配置域名、HTTPS 和代理，选择一条入口

先在域名控制台将 DNS A 记录指向本机公网 IP。只有服务器确实提供 IPv6 时才配置 AAAA。安全组与主机防火墙放行 **80/443**，保持现有 SSH 端口可用；无需开放 **28156**。

以下代理默认使用端口 28156；修改过 APP_PORT 时一并替换。此时容器还未启动，代理返回 502 正常，先完成 HTTPS 再执行第 6 步。

<a id="ubuntu-nginx"></a>

### A. Ubuntu 手动 Nginx + Certbot

由具有 sudo 权限的管理员执行，不需要给 deploy 账号增加 Nginx reload 权限。已有面板管理 Nginx 的服务器直接选 B，不再安装第二套 Nginx。

```bash
sudo apt-get update
sudo apt-get install -y nginx snapd
```

先将下面 `docs.example.cn` 替换成 config.env 的 SITE_DOMAIN，再创建独立站点文件；不要覆盖现有其他站点：

```bash
sudo tee /etc/nginx/sites-available/c156 >/dev/null <<'NGINX'
server {
    listen 80;
    server_name docs.example.cn;
    location / {
        proxy_pass http://127.0.0.1:28156;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Origin $http_origin;
        client_max_body_size 2m;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
NGINX
sudo ln -s /etc/nginx/sites-available/c156 /etc/nginx/sites-enabled/c156
sudo nginx -t
sudo systemctl reload nginx
```

**正常结果：** `nginx -t` 显示 successful，重载无错误。已有 c156 站点或链接时先检查内容，不重复创建。确保其他站点没有重复使用本域名。

未安装 Certbot 时按 [Certbot 官方 Nginx 指引](https://certbot.eff.org/instructions?ws=nginx&os=snap)安装；已有 Certbot 时复用当前安装，不混装 apt 和 snap 版本。首次 snap 安装命令：

```bash
sudo snap install --classic certbot
# 已存在 certbot 命令或该链接时跳过下面这行。
sudo ln -s /snap/bin/certbot /usr/local/bin/certbot
```

把域名替换后申请证书、启用 HTTPS 跳转并验证续签：

```bash
sudo certbot --nginx -d docs.example.cn --redirect
sudo nginx -t
sudo certbot renew --dry-run
systemctl list-timers --all | grep certbot
```

**正常结果：** 证书申请成功、配置校验成功、dry-run 续签成功，存在 certbot 续签定时任务；snap 安装通常为 snap.certbot.renew.timer。保留 Certbot 写入的证书与 HTTP 验证配置。手动入口的 config.env 保持 `NGINX_MANAGED=0`，以后站点或端口调整仍由管理员校验并重载。

<a id="panel-managed"></a>

### B. 已有服务器面板

1. 面板创建本域名网站，申请证书，开启 HTTPS 和 HTTP 跳转 HTTPS。
2. 设置全站反向代理到 `http://127.0.0.1:28156`。
3. 在原有代理 location 中确认以下配置；已有同名指令时修改原项：

```nginx
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Origin $http_origin;
client_max_body_size 2m;
```

保留面板的证书验证 location。由面板保存、校验、重载，并确认自动续签任务已启用。config.env 使用 `NGINX_MANAGED=0`。

已有站点使用 `/home/deploy/c156/nginx/proxy.inc` 时可继续 include，但只能保留一个 `location /` 和一条 `proxy_pass`。使用 include 需让 Nginx 能读取该绝对路径；若部署账号确实具有同一 Nginx 的校验及重载权限，才使用 `NGINX_MANAGED=1` 和正确的 NGINX_BIN。一般首次安装直接使用上述面板自管代理。

**两条入口的正常结果：** `curl -Iv https://YOUR_DOMAIN` 能通过证书校验。应用未启动时可以返回 502；证书错误或连接超时要先修。不要加 `-k`。

**失败定位：** [E08 Nginx 保存或重载失败](TROUBLESHOOTING.md#e08)；[E09 DNS、HTTPS 或证书失败](TROUBLESHOOTING.md#e09)。

## 6. 维护者正式发布，朋友确认网站

**维护者在 GitHub：** 再次 **Run workflow**，选 **main**，`target_environment` 与准备时一致，这次**不勾选 prepare_only**。等三个 job 都变绿。正式部署会先等待容器健康，再在最多 90 秒内每隔 5 秒轮询本机和公网的前端、API，四项都返回本次提交 SHA 才成功。

**朋友在服务器：** 将 `YOUR_DOMAIN` 换成第二步填写的域名。

```bash
curl -fsS https://YOUR_DOMAIN/build-info.json
curl -fsS https://YOUR_DOMAIN/api/healthz
```

**正常结果：** 两条都返回 JSON，`build_sha` 相同，由维护者核对它等于本次发布提交；第二条还有 `"status":"ok"`。

再用浏览器打开 `https://你的域名`，用 **admin** 和第四步的密码登录。新库里没有文档是正常的；如果已有文档，按日常使用确认能打开。

**失败定位：** [E06 Actions 失败](TROUBLESHOOTING.md#e06)；[E10 502 或容器不健康](TROUBLESHOOTING.md#e10)；[E11 登录或 API 403](TROUBLESHOOTING.md#e11)；[E12 版本检查失败](TROUBLESHOOTING.md#e12)。

首次安装到这里结束。以后 Actions 会自动拉取源码、检出本次构建的提交、拉镜像并执行 Compose 更新；没有 releases 目录和版本软链接。推送 main 自动更新 **test**；更新 **prod** 时，维护者手动 Run workflow，选择 **prod**。朋友无需拉代码或手动构建。更新可能有短暂中断。

## 日常只记住这三件事

**下载备份：** 用面板下载 `/home/deploy/c156/backups/` 中已经完成的 `.sqlite` 快照。不要只下载运行中的 `data/c156.sqlite`；它可能还有 WAL 数据。资产目录 `/home/deploy/c156/assets/` 另行打包。源码在克隆的仓库里；Actions 每次会自动拉取并检出本次提交，无需手动 git pull。

**手动备份：** 在服务器复制运行：

```bash
bash -c 'source "$HOME/c156/deploy/common.sh"; load_config "$HOME/c156"; compose_for "$HOME/c156" exec -T backend python -m src.storage backup --database /data/c156.sqlite --output /backups/manual-$(date -u +%Y%m%dT%H%M%S).sqlite'
```

正常时命令无报错结束，backups 中出现新的 `manual-…sqlite`。

**查看容器和日志：**

```bash
cd "$HOME/c156"
source deploy/common.sh
load_config "$PWD"
compose_for "$PWD" ps
compose_for "$PWD" logs --tail 100 backend frontend
```

发布失败不会自动回退。修复当前错误后重新发布；需要恢复旧版本时找维护者处理，见 [E13](TROUBLESHOOTING.md#e13)。不删除数据库或资产。

<a id="panel-proxy"></a>

面板代理设置见[第 5 步 B](#panel-managed)。
