import { ref, onScopeDispose } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, resumeStream, ApiError } from '@/services/api'
import { createRound, type Round, type Settlement } from '@/services/round'
import { getSessionId, setSessionId, onSessionChange } from '@/services/session'
import { useHealth } from '@/composables/useHealth'
import type { AskEvent } from '@/types/events'

// Vue 薄接线层：轮次生命周期/健康状态都在 services/ 的纯模块里（有单测锁行为）。
// 这里只做三件事：把事件喂给 reducer、把轮次终态映射成 reducer 动作、把 SSE 接上 fetch。
//
// 重构要点（相对旧版）：
//   1. 健康探测改用全局单例 useHealth()——侧栏状态灯与提问门控同源，
//      不再一个是 setInterval 轮询、一个是状态机各跑各的；
//   2. 会话 ID 走 services/session.ts 单一事实源，侧栏显示与实际使用不会分叉；
//   3. API Key 支持运行时修改并持久化（localStorage），401/429 时改 key 即可续跑。

const INACTIVITY_MS = 30_000
const DEFAULT_API_KEY = import.meta.env.VITE_API_KEY ?? 'dev-key-001'
const LIVE_PHASES = ['running', 'clarifying', 'approving']
const API_KEY_STORAGE = 'datacrew.apiKey'

function loadApiKey(): string {
  try {
    return localStorage.getItem(API_KEY_STORAGE) || DEFAULT_API_KEY
  } catch {
    return DEFAULT_API_KEY // 存储不可用：内存态照样跑
  }
}

export function useAsk() {
  const state = ref<AskState>({ ...initialState })
  let round: Round | null = null
  const running = ref(false)
  const errorMessage = ref('')
  const apiKey = ref(loadApiKey())
  const sessionId = ref(getSessionId())
  // 会话被别处（侧栏）修改时同步过来：单一事实源，不各持一份
  const offSession = onSessionChange((v) => { sessionId.value = v })
  onScopeDispose(offSession)

  // 健康：全局单例（App 壳与 AskView 共用同一个探测实程）
  const { state: healthState, isDown: healthDown, probeNow } = useHealth()

  function setSessionIdValue(v: string) {
    setSessionId(v) // 模块内广播 → 上面的订阅会把 ref 同步过来
  }

  function setApiKey(v: string) {
    apiKey.value = v
    try {
      localStorage.setItem(API_KEY_STORAGE, v)
    } catch {
      /* 隐私模式：仅内存生效，不阻塞 */
    }
  }

  function fail(msg: string) {
    errorMessage.value = msg
    state.value = askReducer(state.value, { type: 'event', ev: { event: 'error', message: msg } })
  }

  function onSettle(kind: Settlement) {
    if (kind === 'cancelled' && LIVE_PHASES.includes(state.value.phase)) {
      state.value = askReducer(state.value, { type: 'cancel' })
    }
    running.value = false
    round = null
  }

  async function run(gen: AsyncGenerator<AskEvent>): Promise<void> {
    running.value = true
    errorMessage.value = ''
    const r = round
    if (!r) return
    r.arm()
    try {
      for await (const ev of gen) {
        if (r.settled) return // 已被取消/超时收口：迟到的事件不再改状态
        state.value = askReducer(state.value, { type: 'event', ev })
        r.arm() // 每个事件都是"活着"的证据，重置 deadline
      }
      if (r.settled !== 'cancelled' && state.value.phase === 'running') {
        fail('连接中断：流已结束但未收到终态事件（澄清 / 审批 / 结果 / 错误）。请重试或检查后端')
      }
    } catch (e) {
      if (r.settled) return // 取消/超时路径已收口
      if (e instanceof ApiError && e.status === 409) {
        errorMessage.value = '上一轮已失效（后端没有等待恢复的中断了，通常是会话被重启或重复恢复）。请重新提问'
        state.value = askReducer(state.value, { type: 'reset' })
        r.settle('failed')
        return
      }
      const msg = e instanceof ApiError ? `HTTP ${e.status}: ${e.message}` : String(e)
      fail(msg)
      r.settle('failed')
    } finally {
      if (r.settled === null) r.settle('done')
    }
  }

  function newRound(): Round {
    // 上一轮若还在（用户狂点/上一轮挂死），先静默停表取代，不弹状态
    round?.disarm()
    const r = createRound({
      inactivityMs: INACTIVITY_MS,
      onInactivity: () => fail(`连接 ${INACTIVITY_MS / 1000} 秒无新事件，已自动断开（后端无响应或连接被中断）`),
      onSettle,
    })
    round = r
    return r
  }

  function ask(question: string) {
    state.value = askReducer(state.value, { type: 'start', question })
    const r = newRound()
    return run(askStream(question, sessionId.value, apiKey.value, r.signal))
  }

  function answerClarification(answer: string) {
    const r = newRound()
    return run(resumeStream(sessionId.value, answer, apiKey.value, r.signal))
  }

  function decideApproval(approved: boolean) {
    const r = newRound()
    return run(resumeStream(sessionId.value, { approved }, apiKey.value, r.signal))
  }

  // 取消：同步收口（round.cancel 不等 abort 传播，tests/round.test.ts 锁死）
  function cancel() {
    round?.cancel()
  }

  function reset() {
    round?.disarm()
    round?.settle('failed')
    state.value = askReducer(state.value, { type: 'reset' })
    errorMessage.value = ''
    running.value = false
  }

  return {
    state, running, errorMessage, apiKey, setApiKey,
    sessionId, setSessionId: setSessionIdValue,
    healthState, healthDown, probeNow,
    ask, answerClarification, decideApproval, cancel, reset,
  }
}
