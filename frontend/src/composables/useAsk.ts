// useAsk：把纯 reducer + api 客户端接到 Vue 响应式上。
// 职责边界：composable 只做"接线"，判断逻辑都在 reducer/解析器里（已被单测覆盖）。
// 健壮性三件事（都有真实事故驱动）：
//  1. AbortController 必须在发请求前建好并传进流——否则"取消"是死按钮；
//  2. 僵死看门狗：连接被静默掐断（容器重启/代理断）时流既不报错也不结束，
//     N 秒无字节主动 abort 并明确报错，绝不让用户对着一只转圈的按钮；
//  3. EOF 兜底：流正常结束但状态机还停在 running = 后端没给终态事件，按错误收尾。
import { ref } from 'vue'
import { askReducer, initialState, type AskState } from '@/services/reducer'
import { askStream, resumeStream, ApiError } from '@/services/api'
import type { AskEvent } from '@/types/events'

// mock 全链路约 1s；真实 LLM 一轮也远小于此。60s 无字节即可判死。
const INACTIVITY_MS = 60_000

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
        own.abort() // fetch 持有同一个 signal，读取会抛 AbortError
      }, INACTIVITY_MS)
    }
    const disarm = () => { if (timer) { window.clearTimeout(timer); timer = undefined } }
    try {
      arm()
      for await (const ev of gen) {
        state.value = askReducer(state.value, { type: 'event', ev })
        disarm(); arm() // 每个事件都是"还活着"的证据，重置计时
      }
      // EOF 兜底：流结束了但状态机还在 running，说明终态事件没来（连接被掐）
      if (state.value.phase === 'running') {
        fail('连接中断：流已结束但未收到终态事件（澄清 / 审批 / 结果 / 错误）。请重试或检查后端')
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') {
        if (timedOut) {
          fail(`连接 ${INACTIVITY_MS / 1000} 秒无响应，已自动断开（后端不可达或被中断）`)
        }
        return // 主动取消：静默
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
