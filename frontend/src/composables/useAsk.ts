import { reactive, ref, onScopeDispose } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, resumeStream, ApiError } from '@/services/api'
import { createRound, type Round } from '@/services/round'
import { createHealthMonitor, type HealthDeps, type HealthMonitor, type HealthState } from '@/services/health'
import type { AskEvent } from '@/types/events'

// Vue 薄接线层：轮次生命周期/健康状态都在 services/ 的纯模块里（有单测锁行为）。
// 这里只做三件事：把事件喂给 reducer、把轮次终态映射成 reducer 动作、把健康探针接上 fetch。

const INACTIVITY_MS = 30_000
const HEALTH_INTERVAL_MS = 15_000
const HEALTH_TIMEOUT_MS = 6_000
const DEFAULT_API_KEY = import.meta.env.VITE_API_KEY ?? 'dev-key-001'
const LIVE_PHASES = ['running', 'clarifying', 'approving']

function probeHealth(signal: AbortSignal): Promise<boolean> {
  return fetch('/health', { signal })
    .then((r) => r.ok)
    .catch(() => false)
}

export function useAsk() {
  const state = ref<AskState>({ ...initialState })
  const running = ref(false)
  const errorMessage = ref('')
  const apiKey = ref(DEFAULT_API_KEY)
  const sessionId = ref(localStorage.getItem('datacrew.sessionId') || 'demo-001')
  let round: Round | null = null

  const healthOptions: HealthDeps = {
    probe: probeHealth,
    intervalMs: HEALTH_INTERVAL_MS,
    probeTimeoutMs: HEALTH_TIMEOUT_MS,
  }
  const health: HealthMonitor = createHealthMonitor(healthOptions)
  // Vue 层自持响应式状态，纯模块经回调通知（不能 reactive(health.state)：
  // 纯模块按原始引用改对象，代理 setter 不触发——界面永远停在“探测中”，
  // 见 tests/health.reactive.test.ts 的回归用例）。
  const healthState = reactive<HealthState>({ ...health.state })
  healthOptions.onStateChange = (s: HealthState) => Object.assign(healthState, s)
  health.start()
  onScopeDispose(() => health.stop())

  function setSessionId(v: string) {
    sessionId.value = v
    localStorage.setItem('datacrew.sessionId', v)
  }

  function fail(msg: string) {
    errorMessage.value = msg
    state.value = askReducer(state.value, { type: 'event', ev: { event: 'error', message: msg } })
  }

  function onSettle(kind: string) {
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
        r.arm() // 每个事件都是“活着”的证据，重置 deadline
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
    state, running, errorMessage, apiKey, sessionId, setSessionId,
    healthState, ask, answerClarification, decideApproval, cancel, reset,
  }
}
