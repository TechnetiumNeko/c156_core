import { ApiError } from '../api/client.ts';
export function errorText(error: unknown) {
  if (error instanceof ApiError) return ({unauthenticated: '登录已失效，请重新登录。', forbidden: '你没有权限执行此操作。', frozen: '文档已冻结，暂时无法修改。', conflict: '内容或权限已变化，请刷新后再试。', already_exists: '这个名称已被使用，请换一个。', network: '无法连接服务器，请检查连接后重试。', response: '服务器响应无法读取，请重试。', invalid_argument: '输入不符合要求，请检查名称、角色或密码。', invalid_request: '输入不符合要求，请检查后重试。', rate_limited: '操作过于频繁，请稍后再试。', not_found: '内容不存在或已无法访问，请刷新目录。'} as Record<string, string>)[error.code] ?? '操作未完成，请检查输入后重试。';
  return error instanceof Error ? error.message : '操作未完成，请重试。';
}

export function deleteErrorText(error: unknown, folder: boolean, submitted: boolean) {
  const target = folder ? '文件夹' : '文档';
  if (error instanceof ApiError) {
    if (error.code === 'forbidden') return folder
      ? '无法删除文件夹：你没有删除目录或其中全部内容的权限。本次删除未执行，内容仍保留。请联系工作区管理员处理。'
      : '你没有删除这篇文档的权限，本次删除未执行。';
    if (error.code === 'frozen') return folder
      ? '文件夹中有冻结文档，本次删除未执行。请由冻结者或工作区管理员解除冻结后重试。'
      : '文档已冻结，本次删除未执行。请解除冻结后重试。';
    if (error.code === 'conflict') return `${target}已变化，本次删除未执行。请重新打开删除操作，确认最新内容后再删除。`;
    if (error.code === 'not_found') return `${target}不存在或已无法访问，本次删除未执行。请刷新目录确认。`;
    if (error.code === 'network' || error.code === 'response') return submitted
      ? '删除结果尚未确认。请刷新目录核对，勿直接重复删除；当前草稿仍保留。'
      : '无法读取删除所需的信息，本次删除未执行。请检查连接后重试。';
  }
  return errorText(error);
}
