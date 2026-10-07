# 部署重试与首次安装手册验证

日期：2026-10-07。基于已确认的直接 clone + 根目录配置 + 双容器 Compose 部署，未更改数据库、认证、TLS 或发布入口架构。

改动：

- `deploy.sh` 的 Git fetch 最多 3 次；每次 timeout 90 秒，终止宽限 5 秒，失败后等待 5 秒。输入来自 `/dev/null`，不会消耗 SSH 流式脚本；仍先拒绝受跟踪文件的本地修改，并检出被测试的准确 SHA。
- Compose 健康等待后，发布进程最多轮询 90 秒，间隔 5 秒。本机与公网的 build-info/API healthz 全部匹配本次 SHA 才更新成功序号；每次 curl 最大 5 秒且不超出剩余窗口，保持 HTTPS 证书校验。不新增长期监控进程。
- 首次安装手册保留六步：clone/安全更新、setup、prepare、显式初始化管理员、配置入口、正式发布。入口提供 Ubuntu 手动 Nginx sites-available/sites-enabled + Certbot 和已有面板两条路径，模板推荐 `NGINX_MANAGED=0`。已有配置仍保留原值。
- 排错补充 GitHub 重试耗尽、15～128 字符管理员密码及仅引导失败后的恢复、Nginx reload 权限、证书 secondary DNS SERVFAIL、发布健康超时。

本地执行及结果：

```text
/data/sunyunbo/www/c156_core/.worktrees/vue-fastapi-loop/.venv/bin/python -m unittest tests.test_deployment -v
Ran 10 tests in 10.660s — OK

bash -n deploy/common.sh deploy/deploy.sh deploy/setup.sh
退出 0

/tmp/c156-check-tools/shellcheck-v0.11.0/shellcheck -x -P deploy deploy/common.sh deploy/deploy.sh deploy/setup.sh
退出 0

git diff --check
退出 0
```

新增验证直接调用生产脚本/函数：本地真实 Git remote 先不可访问，等待生产脚本实际输出第一次 fetch 失败日志后恢复，第二次 fetch 成功并保留数据库；两个真实 HTTP 服务模拟本机与公网入口，公网初始旧 SHA 后恢复时轮询成功，持续旧 SHA 时限时失败。HTTP 用例仅对本地测试服务设置 NO_PROXY，避免工作环境的 HTTP 代理干扰本地连接。保留准确 SHA、本地源码修改保护、stale sequence 和 setup 身份配置的已有行为验证。

未在 ECS 上执行本次脚本，未通过本次验证实际申请证书、执行 Nginx reload 或检查朋友面板。新代码需要合并后由 Actions 在 test 发布，再核对公网 SHA；旧版本已成功部署不能替代本次发布验证。手册 Certbot 路径核对 [Certbot 官方 Nginx 指引](https://certbot.eff.org/instructions?ws=nginx&os=snap)。
