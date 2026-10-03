// useAsk：把纯 reducer + api 客户端接到 Vue 响应式上（只做接线）。
// API Key 不暴露给用户：界面上没有输入框，默认 dev-key-001（构建期可用
// VITE_API_KEY 覆盖）。想换 key 的人改 env，普通用户不该被一个密码框困扰。
//
// 健壮性（都由真实事故驱动，一次修三处）：
//  1. controller 必须在发请求前建好并传进流；
//  2. 硬超时看门狗：30s 无事件——无论底层 abort 有没有传播——UI 都必须收口；
//  3. EOF 兜底：流结束但状态机还在 running = 终态丢了，按错误收尾；
//  4. 取消 = 明确状态：取消后停在“已取消”并保留轨迹，不会假装还在跑。
import { ref } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, resumeStream, ApiError } from '@/services/api'
import type { AskEvent } from '@/types/events'

// mock 全链路 <2s；真实 LLM 一轮通常也 <20s。30s 无事件即可判死，
// 宁可误判重试，也不让用户对着一只永远转圈的按钮。
const INACTIVITY_MS = 30_000
const DEFAULT_API_KEY = import.meta.env.VITE_API_KEY ?? 'dev-key-001'

export function useAsk() {
  const state = ref<AskState>({ ...initialState })
  const running = ref(false)
  const errorMessage = ref('')
  const apiKey = ref(DEFAULT_API_KEY)
  const sessionId = ref(localStorage.getItem('datacrew.sessionId') || 'demo-001')
  let controller: AbortController | null = null

  function setSessionId(v: string) {
    sessionId.value = v
    localStorage.setItem('datacrew.sessionId', v)
  }

  function fail(msg: string) {
    errorMessage.value = msg
    state.value = askReducer(state.value, { type: 'event', ev: { event: 'error', message: msg } })
  }

  async function run(gen: AsyncGenerator<AskEvent>, own: AbortController): Promise<void> {
    running.value = true
    errorMessage.value = ''
    let timedOut = false
    let timer: number | undefined
    const arm = () => {
      timer = window.setTimeout(() => {
        timedOut = true
        own.abort()
        // 关键：即使 abort 没能传播到 pending read()（代理层吞掉时），
        // UI 也必须在这里收口，给出明确错误。
        fail(`连接 ${INACTIVITY_MS / 1000} 秒无新事件，已自动断开（后端无响应或连接被中断）`)
      }, INACTIVITY_MS)
    }
    const disarm = () => { if (timer) { window.clearTimeout(timer); timer = undefined } }
    try {
      arm()
      for await (const ev of gen) {
        state.value = askReducer(state.value, { type: 'event', ev })
        disarm(); arm()
      }
      if (state.value.phase === 'running') {
        fail('连接中断：流已结束但未收到终态事件（澄清 / 审批 / 结果 / 错误）。请重试或检查后端')
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') {
        if (timedOut) return // 超时路径已由 fail() 收口
        state.value = askReducer(state.value, { type: 'cancel' })
        return // 主动取消：静默切换为“已取消”
      }
      const msg = e instanceof ApiError ? `HTTP ${e.status}: ${e.message}` : String(e)
      fail(msg)
    } finally {
      disarm()
      running.value = false
      controller = null
    }
  }

  function ask(question: string) {
    state.value = askReducer(state.value, { type: 'start', question })
    controller = new AbortController()
    return run(askStream(question, sessionId.value, apiKey.value, controller.signal), controller)
  }

  function answerClarification(answer: string) {
    controller = new AbortController()
    return run(resumeStream(sessionId.value, answer, apiKey.value, controller.signal), controller)
  }

  function decideApproval(approved: boolean) {
    controller = new AbortController()
    return run(resumeStream(sessionId.value, { approved }, apiKey.value, controller.signal), controller)
  }

  function cancel() { controller?.abort() }

  function reset() {
    controller?.abort()
    state.value = askReducer(state.value, { type: 'reset' })
    errorMessage.value = ''
    running.value = false
  }

  return {
    state, running, errorMessage, apiKey, sessionId, setSessionId,
    ask, answerClarification, decideApproval, cancel, reset,
  }
}
