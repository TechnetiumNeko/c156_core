# 部署操作手册

第一次部署按下面 **1 → 6** 做。每步看到“正常结果”再继续；报错就点该步的排错链接。以后更新由 GitHub Actions 完成。

本文使用 `/srv/c156` 和 `28156`，适用于已有 Docker、Compose、Nginx 和证书面板的服务器。你的 Ubuntu ECS 使用 `test`，朋友服务器使用 `prod`；GitHub 参数放在 Repository，分别以 TEST_／PROD_ 开头；两台分别执行同一套步骤，无需安装 Python、Node 或 Certbot。

## 开始前：维护者把东西准备好

维护者先完成[维护者配置](MAINTAINER.md#maintainer)，再把以下内容交给朋友：

| 交给朋友 | 填什么 |
| --- | --- |
| 部署文件 | `c156-deploy.zip`，解压后包含 `deploy/setup.sh` 等文件 |
| 部署环境 | 你的服务器填 `test`，朋友服务器填 `prod`；后面两次 Run workflow 都选这个环境 |
| 网站域名 | 一个确定的域名，例如 `docs.example.cn` |
| ACR 公网域名 | 从控制台复制，不带 `https://` 或仓库路径 |
| ACR 用户名和登录密码 | 用于服务器首次 `docker login`；通过私下渠道交付 |
| 服务器登录账号 | 与 GitHub 的 `DEPLOY_USER` 相同，能操作 Docker、项目目录及 Nginx |
| 联系人 | 遇到 Actions 或 SSH 报错时找谁 |

中国内地服务器上线前先确认域名备案已完成。[阿里云说明](https://help.aliyun.com/zh/icp-filing/basic-icp-service/support/for-the-record-process-faq)

## 1. 朋友：上传文件，检查服务器

用上表的服务器账号登录。用面板文件管理器将压缩包上传、解压到 `/tmp/c156-deploy`。

**在服务器终端逐行运行：**

```bash
ls /tmp/c156-deploy/deploy/setup.sh
docker info >/dev/null && echo 'Docker OK'
docker compose version
nginx -v
command -v curl flock tar rsync ss
```

**正常结果：** 第一行显示文件路径；随后有 `Docker OK`、Compose 和 Nginx 版本；最后显示五个命令的路径。

如果 `nginx -v` 找不到命令，但面板的 Nginx 正常运行，记录面板 Nginx 的实际路径，下一步填 `NGINX_BIN`。其他命令缺失或权限报错，先解决再继续。

**失败定位：** [E01 文件位置不对](TROUBLESHOOTING.md#e01)；[E02 组件缺失或权限不足](TROUBLESHOOTING.md#e02)。

## 2. 朋友：生成配置，只改域名

**先运行：**

```bash
bash /tmp/c156-deploy/deploy/setup.sh /srv/c156
```

**第一次的正常结果：** 显示 `Edit SITE_DOMAIN in /srv/c156/config.env`。这是提示你改配置，尚未创建应用数据目录。若配置已存在，脚本直接检查配置；按下一段检查即可。

用面板文件管理器编辑 `/srv/c156/config.env`，把这一行改成真实域名：

```dotenv
SITE_DOMAIN=docs.example.cn
```

这里只填域名：**不加 `https://`，不加 `/`，不加引号或空格**。其他设置通常不用改。

如果第一步记录了面板 Nginx 路径，再改这一行，例如：

```dotenv
NGINX_BIN=/www/server/nginx/sbin/nginx
```

保存后运行：

```bash
bash /tmp/c156-deploy/deploy/setup.sh /srv/c156
```

**正常结果：** 最后一行包含 `Directories ready`，并列出 data、assets、backups 的路径。

**失败定位：** [E03 配置、端口或网络](TROUBLESHOOTING.md#e03)；[E02 权限或组件](TROUBLESHOOTING.md#e02)；[E08 Nginx 路径](TROUBLESHOOTING.md#e08)。

## 3. 朋友登录镜像仓库，维护者准备镜像

**朋友在服务器运行：** 将下面 `YOUR_ACR_REGISTRY` 换成维护者给的 ACR 公网域名。

```bash
docker login YOUR_ACR_REGISTRY
```

按提示输入 ACR 用户名和密码。密码不要写进命令。

**正常结果：** `Login Succeeded`。然后告诉维护者：“服务器已准备好，可以准备镜像了。”

**维护者在 GitHub 操作：**

1. 仓库 → **Actions** → **Test, build and deploy** → **Run workflow**。
2. 分支选 **main**；`target_environment` 选本台服务器的 **test** 或 **prod**；勾选 **prepare_only**，点 **Run workflow**。
3. 等 `checks`、`images`、`deploy` 都变绿，再通知朋友继续。

**朋友确认：**

```bash
ls /srv/c156/prepared/compose.yaml
```

**正常结果：** 显示 `/srv/c156/prepared/compose.yaml`。这一步只准备文件和镜像，网站尚未启动。

**失败定位：** [E04 ACR 登录或拉镜像失败](TROUBLESHOOTING.md#e04)；[E05 SSH 失败](TROUBLESHOOTING.md#e05)；[E06 Actions 或 prepared 缺失](TROUBLESHOOTING.md#e06)。

## 4. 朋友：创建新库和管理员

**仅第一次创建空库时运行：**

```bash
bash /srv/c156/prepared/setup.sh /srv/c156 --init-db
```

看到 `Password:` 后输入网站管理员密码；看到 `Confirm password:` 再输一次。输入时屏幕不显示字符，这是正常的。网站账号固定为 **admin**，密码是这里设置的，与 ACR 密码无关。

**正常结果：** 命令成功结束，最后包含 `Directories ready`。之后不要再运行 `--init-db`。

如果已经有数据库，或曾经运行到一半，不要删库重来，按下面的排错项处理。

**失败定位：** [E07 初始化中断、已有库或管理员失败](TROUBLESHOOTING.md#e07)。

## 5. 朋友：面板配置域名、HTTPS 和代理

**在面板及域名控制台操作：**

1. 域名的 DNS A 记录指向这台服务器公网 IP。
2. 面板创建该域名的网站，申请证书，开启 HTTPS 和 HTTP 跳转 HTTPS。
3. 安全组、主机防火墙允许 **80/443**；**无需开放 28156**。
4. 在该站点的 Nginx 配置中找到已有的 `location / { ... }`。让它使用下面的项目 include：

```nginx
location / {
    include /srv/c156/nginx/proxy.inc;
}
```

这是该 location 的示例，**不要在已有 `location /` 旁再添加第二个，也不要在其中保留另一条 `proxy_pass`**。其他 location，特别是面板的证书验证入口，保持原样。保存并由面板校验、重载。

**正常结果：** 面板保存成功，证书匹配域名，没有浏览器证书警告。此时应用尚未启动，页面出现 **502 可以先继续第 6 步**；证书警告、域名不通或 Nginx 保存失败要先修复。

再检查面板是否已经启用自动续签任务。当前尚未确认朋友面板会自动续签，不能仅凭证书申请成功就略过。

面板不支持 include 时，按[面板自管代理接法](#panel-proxy)操作，再继续第 6 步。

**失败定位：** [E08 Nginx 保存或重载失败](TROUBLESHOOTING.md#e08)；[E09 域名、HTTPS 或证书失败](TROUBLESHOOTING.md#e09)。

## 6. 维护者正式发布，朋友确认网站

**维护者在 GitHub：** 再次 **Run workflow**，选 **main**，`target_environment` 与准备时一致，这次**不勾选 prepare_only**。等三个 job 都变绿。

**朋友在服务器：** 将 `YOUR_DOMAIN` 换成第二步填写的域名。

```bash
curl -fsS https://YOUR_DOMAIN/build-info.json
curl -fsS https://YOUR_DOMAIN/api/healthz
```

**正常结果：** 两条都返回 JSON，`build_sha` 相同，由维护者核对它等于本次发布提交；第二条还有 `"status":"ok"`。

再用浏览器打开 `https://你的域名`，用 **admin** 和第四步的密码登录。新库里没有文档是正常的；如果已有文档，打开并保存一次确认。

**失败定位：** [E06 Actions 失败](TROUBLESHOOTING.md#e06)；[E10 502 或容器不健康](TROUBLESHOOTING.md#e10)；[E11 登录或 API 403](TROUBLESHOOTING.md#e11)；[E12 版本检查失败](TROUBLESHOOTING.md#e12)。

首次安装到这里结束。以后推送 main 自动更新 **test**；更新 **prod** 时，维护者手动 Run workflow，选择 **prod**。朋友无需拉代码或手动构建。更新可能有短暂中断。

## 日常只记住这三件事

**下载备份：** 用面板下载 `/srv/c156/backups/` 中已经完成的 `.sqlite` 快照。不要只下载运行中的 `data/c156.sqlite`；它可能还有 WAL 数据。资产目录 `/srv/c156/assets/` 另行打包。源码在每个 release 的 `source.tar.gz`，无需服务器 `git pull`。

**手动备份：** 在服务器复制运行：

```bash
bash -c 'source /srv/c156/current/common.sh; load_config /srv/c156; compose_for /srv/c156/current exec -T backend python -m src.storage backup --database /data/c156.sqlite --output /backups/manual-$(date -u +%Y%m%dT%H%M%S).sqlite'
```

正常时命令无报错结束，backups 中出现新的 `manual-…sqlite`。

**回退上一次成功发布：**

```bash
bash /srv/c156/current/rollback.sh /srv/c156
```

正常时最后显示 `Deployed …`。只有一个成功版本时无法回退。回退保留当前数据库和资产，不用旧快照覆盖新数据。

以上操作报错，或磁盘快满了，查 [E13 备份、回退和空间](TROUBLESHOOTING.md#e13)。

<a id="panel-proxy"></a>

## 面板自管代理接法：仅 include 不可用时看

编辑 `/srv/c156/config.env`：

```dotenv
NGINX_MANAGED=0
```

在面板设置**全站反向代理**到 `http://127.0.0.1:28156`。代理 location 中使用以下设置；面板已生成同名指令时修改原项，避免重复粘贴：

```nginx
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Origin $http_origin;
client_max_body_size 2m;
```

由面板保存、校验和重载，保留其证书与 ACME 配置。端口改过时，将上述代理地址的端口一起改掉。自动部署仍检查 HTTPS，但不更新代理文件、不重载 Nginx。
