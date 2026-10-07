import { ApiError } from '../api/client.ts';
export function errorText(error: unknown) {
  if (error instanceof ApiError) return ({unauthenticated: '登录已失效，请重新登录。', forbidden: '你没有权限执行此操作。', frozen: '文档已冻结，暂时无法修改。', conflict: '内容或权限已变化，请刷新后再试。', already_exists: '同一目录下已有这个名称，请换一个。', network: '无法连接服务器，请检查连接后重试。', response: '服务器响应无法读取，请重试。', invalid_argument: '输入不符合要求，请检查名称、角色或密码。', invalid_request: '输入不符合要求，请检查后重试。', rate_limited: '操作过于频繁，请稍后再试。', not_found: '内容不存在或已无法访问，请刷新目录。'} as Record<string, string>)[error.code] ?? '操作未完成，请检查输入后重试。';
  return error instanceof Error ? error.message : '操作未完成，请重试。';
}
