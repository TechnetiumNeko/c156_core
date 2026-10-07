# Compose 部署交付验证

实施分支：`feat/compose-deploy`。生产配置与容器代码已完成；独立整分支审阅后的修复提交为 `856d0df`。不连接朋友服务器，不推送 ACR 或触发真实部署。

## 实际运行的检查

| 命令／检查 | 结果 |
| --- | --- |
| Python 3.13：`python -m unittest discover -s tests -q` | 485 项通过；之后新增的发布指针回归用例与相关检查一起验证 |
| `C156_JSDOM_PATH=/tmp/c156-web-checks/node_modules/jsdom node --test tests/web/*.test.mjs` | 16 项通过，真实 jsdom，未跳过 |
| frontend：`npm ci`、`npm test`、`npm run typecheck`、`npm run build` | 22 项测试、类型检查与构建通过 |
| `python -m unittest tests.test_deployment tests.test_backup tests.test_server_production -q` | 最后修复后的 9 项相关检查通过 |
| `docker compose -f deploy/compose.yaml config --quiet`，带必需配置 | 通过；只有前端绑定 loopback 高位端口 |
| 两个 Dockerfile 实际 `docker build` | 后端、前端最终镜像成功构建；基础镜像使用官方 digest |
| `BUILD_SHA=… BACKEND_IMAGE=… FRONTEND_IMAGE=… python deploy/smoke.py` | 实际 frontend→FastAPI：版本、健康、Secure Cookie、登录、保存、重建后读取、退出通过 |
| `bash -n deploy/*.sh` | 通过 |
| actionlint 1.7.12 + shellcheck 0.11.0 | 工作流及部署 Bash 检查通过 |
| `bash deploy/package.sh OUTPUT SHA BACKEND_DIGEST FRONTEND_DIGEST SEQUENCE` | 最终提交源码归档与交付脚本逐字一致，release.env SHA 正确 |

本机系统 Docker socket 对当前账号不可用，但已安装 rootless 工具和用户 UID/GID 映射。使用独立临时 rootless Docker 29.0.2 引擎完成上述检查，未修改系统 Docker 权限。测试使用一次性可写目录适配 rootless UID 映射；生产 setup 仍为指定 APP_UID/GID 和 0700 目录。

## 真实失败恢复检查

使用一次性脚本调用原始 `deploy/deploy.sh`，搭配真实临时 Distribution Registry、两组不同 build SHA 的最终镜像、独立 Nginx 1.18 和临时 TLS 证书。通过 curl 的仅测试配置将 HTTPS 443 请求连接到隔离高位端口，并验证证书；没有替换 curl、Docker 或 Nginx 为 mock。

实际观测结果：

- 首次候选 Nginx 配置非法：明确报告没有上一版本，未创建 current/previous。
- 首次成功：前后端 SHA 与真实 HTTPS 检查通过，未生成虚假的 previous。
- 后续候选 `nginx -t` 失败：原代理片段、旧镜像、current/previous 和发布序号恢复。
- 成功记录暂存文件指向 `/dev/full`：真实写入失败后旧服务恢复，成功记录不变。
- current 新链接位置存在文件：真实链接发布失败后，已改变的 previous 被恢复，旧服务正常。
- 真正无法启动的候选后端：Compose 健康检查失败，旧容器恢复，成功记录不变。
- 所有失败后，实际 ContentService 仍读到原用户正文，部署前已生成经过校验的 SQLite 快照。
- 经过外层 Nginx、前端代理和 Uvicorn，伪造 X-Forwarded-For 被覆盖；实际认证限流来源是 127.0.0.1，未采用伪造来源。

最后停止并移除了本次测试容器、网络、临时 Registry 和 Nginx。临时引擎也在收尾时停止。

## 审阅及修复

独立 reviewer 使用全新上下文审阅整分支。无 Critical；发现发布重跑覆盖元数据、首次虚假上一版本、状态提交失败不恢复指针三个重要问题，以及失败恢复证据缺口。修复采用唯一 run attempt 发布目录、有效 release 链接解析、提交前状态暂存及失败链接还原；上述真实检查验证了结果。未另加永久大型部署测试框架。

## 尚未验证

- 真实 GitHub Actions 云端执行、ACR 个人版推送及 ECS SSH 部署：尚未提供实际 variables/secrets，未尝试。
- 朋友面板的具体配置界面、Nginx 1.26 主机权限、真实公网域名/DNS/证书申请及自动续签。
- 真实浏览器 HTTPS 登录和保存；未进行浏览器自动交互。上线手册包含首次人工确认步骤。
- ARM、多节点、零停机和自动数据库迁移不属于本次交付。

实际入口和初次交接见 [部署手册](../../deploy/README.md)，报错处理见 [排错清单](../../deploy/TROUBLESHOOTING.md)。

## 后续调整：test/prod 独立环境

用户确认：自己的 Ubuntu ECS 为 test，朋友服务器为 prod；推送 main 自动发布 test，prod 手动 Run workflow 选择。工作流增加 target_environment 和独立部署并发组，ACR/SSH 参数全部由对应 GitHub Environment 提供，DEPLOY_ENVIRONMENT 校验目标一致性。旧 Repository 同名项需要迁移并移除，避免上层配置继承。

检查与构建任务不绑定部署 Environment。实际 smoke 通过的镜像保存为带 SHA/attempt 的 artifact，部署任务按生产者输出的 artifact ID 下载、校验、加载，再推送本环境 ACR。独立审阅指出仅重跑部署 job 时 run_attempt 会变化，按当前 attempt 拼下载名会失败；使用生产者 artifact ID 修复该问题。

本次验证：

- actionlint 1.7.12 搭配 shellcheck 0.11.0 检查工作流通过。
- 直接执行工作流的配置检查 Bash：test/prod 启用、自动未启用、手动未启用、错误环境标记、缺失旧环境标记、缺失 SSH Key、非法目标，共 8 种场景通过。
- 在独立临时 rootless 引擎运行实际工作流 save/load 命令：两个已验证最终镜像的 ID 和 revision 均保持一致；故意破坏 tar 后在校验处失败，不继续加载。
- 51 个文档内部链接／锚点及 18 段 Bash 语法通过，git diff --check 通过。

未重新执行应用全套测试或镜像业务 smoke：本次不修改应用、Dockerfile、Compose 或服务器部署脚本，相关证据沿用上文。未创建真实 GitHub Environments、填写 Secrets 或执行 ACR/ECS 发布；GitHub 云端 artifact 传输、部分重跑及 Environment 凭据选择仍需首次实际运行确认。临时本地引擎在验证后停止。

## 最新调整：Repository TEST_/PROD_ 方案

用户确认改用 Repository 级 Variables/Secrets，避免个人仓库 Environment 配置仅限所有者的权限障碍。已删除部署 job 的 environment 绑定，全部参数通过所选目标的 TEST_／PROD_ 名称读取；保留 main 自动 test、手动 prod、独立并发组及生产者 artifact ID。

独立只读审阅未发现新的具体缺陷，确认没有跨目标或旧无前缀配置回退。actionlint 搭配 shellcheck 通过；直接运行当前配置检查 Bash 的 7 个场景通过（test/prod 启用、自动未启用、手动 prod 未启用、缺失 prod SSH、缺失 test host、非法目标），缺失提示显示正确的完整 Repository 参数名。51 个文档链接／锚点及 18 段 Bash 语法通过，git diff --check 通过。

本次未修改应用或镜像传递／服务器部署逻辑，未重复应用、镜像或回退检查。未填写真实 Repository 值、执行 GitHub 云端发布、连接 ACR/ECS。维护者需按照最新配置表填写前缀项；旧阶段的 Environment 配置说明已被当前方案替代。

## 最新收尾：简化参数及首次安装

用户取消固定主机身份校验，工作流和 rsync 使用一致的 SSH 参数，不再要求 DEPLOY_KNOWN_HOSTS。上游检查已确认 test 的 6 个 Variables 与 4 个必需 Secret 名称全部存在；Variables 基础格式通过。Secret 值不可回读，尚未进行真实 ACR/SSH 认证。

用户将默认目录改为部署用户 HOME/c156。setup 初次生成 config.env 会写入实际选择的绝对目录，rollback 使用同一默认目录，CI 未设置显式目录时查询远端 SSH 用户的 HOME。新增真实 setup 回归用例确认自定义目录无需再手改 config.env；路径校验拒绝根目录等价写法、点路径和尾斜杠，相关部署测试共 6 项通过。独立审阅指出根目录别名问题，已修复。

首次操作按用户修正为直接 git clone 到 ~/c156，填根目录 config.env；不新增源码交接目录。文档以 TEST 起步清单列出 8 项必填配置、2 项可选配置，原有 10 项都可继续保留。

本次未修改运行中的服务器、数据库或部署凭据值，未执行真实部署。取消的旧主机记录参数已不被工作流使用。历史章节中的服务器严格主机校验和 /srv 默认值已由上述用户确认替代。
