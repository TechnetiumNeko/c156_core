# 浏览器登录初始化失败

现象：已部署站点登录页显示 Request could not be completed，登录按钮不可用。正式部署提交 9671aac 的 /api/bootstrap 经公开只读 GET 返回 200；未登录 /api/session 返回预期 401。

根因：ApiClient 的默认 transport 直接保存原生 fetch，再以 this.transport(...) 调用。Chromium 把 ApiClient 作为 fetch 的接收者，抛出 Illegal invocation。前端因此未能取得 nonce，登录按钮保持禁用。

修复：默认 transport 改为箭头函数包装 fetch，保持相对路径、same-origin credentials、nonce/CSRF、Secure Cookie 和代理检查不变。没有修改后端认证或服务器配置。

验证：

- 使用实际生产 ApiClient.ts、本地 HTTP 初始化响应和原生 Chromium fetch。修复前返回 network / Request could not be completed，修复后成功得到初始化响应并保存 nonce。未操作网站按钮或填写表单。
- 现有前端 npm test：22 个用例通过；typecheck、build 通过。
- 未新增浏览器依赖或只验证 mock 的永久测试。一次性脚本为 /tmp/c156-fetch-regression.mjs。

真实站点尚需部署新版镜像；本次未使用真实密码登录或更改真实用户数据。
