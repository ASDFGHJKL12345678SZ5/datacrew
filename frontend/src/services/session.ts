// ===== 会话 ID：单一事实源（localStorage 持久化 + 订阅通知） =====
// 为什么存在：侧栏要显示当前会话、useAsk 要读写它——两边各持一份就会
// "显示一个、实际用另一个"。抽成模块级单一来源，读写都走这里。
// 纯模块：不 import vue；localStorage 不可用时（SSR/测试）自动降级内存态。
//
// 契约提醒：session_id 是 checkpointer 的 thread_id——同一 ID 的状态跨请求延续，
// 澄清/审批中断的恢复完全依赖它。改它 = 开一条新会话线。

const STORAGE_KEY = 'datacrew.sessionId'
const DEFAULT_ID = 'demo-001'

function store(): Storage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null // 隐私模式/禁用存储：内存态兜底，功能不碎
  }
}

let current: string = store()?.getItem(STORAGE_KEY) || DEFAULT_ID
const listeners = new Set<(v: string) => void>()

export function getSessionId(): string {
  return current
}

export function setSessionId(v: string): void {
  const next = v.trim() || DEFAULT_ID
  if (next === current) return
  current = next
  store()?.setItem(STORAGE_KEY, next)
  listeners.forEach((fn) => fn(next))
}

/** 订阅会话切换（返回取消订阅函数）。 */
export function onSessionChange(fn: (v: string) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
