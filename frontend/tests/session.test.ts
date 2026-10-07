import { afterEach, describe, expect, it } from 'vitest'
import { getSessionId, onSessionChange, setSessionId } from '@/services/session'

// node 环境无 localStorage → 模块走内存兜底，正好验证降级路径也能用
afterEach(() => setSessionId('demo-001'))

describe('session 单一事实源', () => {
  it('默认 ID 与读写', () => {
    expect(getSessionId()).toBe('demo-001')
    setSessionId('session-42')
    expect(getSessionId()).toBe('session-42')
  })

  it('setSessionId 广播给订阅者（侧栏/视图同步的依据）', () => {
    const seen: string[] = []
    const off = onSessionChange((v) => seen.push(v))
    setSessionId('a')
    setSessionId('b')
    off()
    setSessionId('c') // 退订后不再收到
    expect(seen).toEqual(['a', 'b'])
    expect(getSessionId()).toBe('c')
  })

  it('空白 ID 回退默认值（不允许清空成空洞会话）', () => {
    setSessionId('x')
    setSessionId('   ')
    expect(getSessionId()).toBe('demo-001')
  })
})
