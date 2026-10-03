// useAsk：把纯 reducer + api 客户端接到 Vue 响应式上。
// 职责边界：composable 只做"接线"，判断逻辑都在 reducer/解析器里（已被单测覆盖）。
import { ref } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, resumeStream, ApiError } from '@/services/api'
import type { AskEvent } from '@/types/events'

export function useAsk() {
  const state = ref<AskState>({ ...initialState })
  const running = ref(false)
  const errorMessage = ref('')
  const apiKey = ref(import.meta.env.VITE_API_KEY ?? '')
  const sessionId = ref(localStorage.getItem('datacrew.sessionId') || 'demo-001')
  let controller: AbortController | null = null

  function setSessionId(v: string) {
    sessionId.value = v
    localStorage.setItem('datacrew.sessionId', v)
  }

  async function run(gen: AsyncGenerator<AskEvent>): Promise<void> {
    controller = new AbortController()
    running.value = true
    errorMessage.value = ''
    try {
      for await (const ev of gen) {
        state.value = askReducer(state.value, { type: 'event', ev })
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return // 主动取消，静默
      const msg = e instanceof ApiError ? `HTTP ${e.status}: ${e.message}` : String(e)
      errorMessage.value = msg
      state.value = askReducer(state.value, { type: 'event', ev: { event: 'error', message: msg } })
    } finally {
      running.value = false
      controller = null
    }
  }

  function ask(question: string) {
    state.value = askReducer(state.value, { type: 'start', question })
    return run(askStream(question, sessionId.value, apiKey.value, controller?.signal))
  }

  function answerClarification(answer: string) {
    return run(resumeStream(sessionId.value, answer, apiKey.value, controller?.signal))
  }

  function decideApproval(approved: boolean) {
    return run(resumeStream(sessionId.value, { approved }, apiKey.value, controller?.signal))
  }

  function cancel() {
    controller?.abort()
  }

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
