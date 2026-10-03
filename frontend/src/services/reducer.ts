// 一次"提问轮次"的状态机（纯函数：state + action -> state，可单测、可时间旅行调试）。
// 与后端边界：这里的 action 只来自 SSE 事件，不掺任何网络/UI 细节。
import type { AskEvent, ClarificationEvent, ApprovalEvent, ResultEvent, NodeDoneEvent } from '@/types/events'

export type Phase = 'idle' | 'running' | 'clarifying' | 'approving' | 'done' | 'error'

export interface TimelineEntry {
  node: string
  latency_ms?: number
  detail?: Record<string, unknown>
}

export interface AskState {
  phase: Phase
  question: string
  timeline: TimelineEntry[]
  clarification?: ClarificationEvent
  approval?: ApprovalEvent
  result?: ResultEvent
  error?: string
}

export const initialState: AskState = {
  phase: 'idle',
  question: '',
  timeline: [],
}

export type AskAction =
  | { type: 'start'; question: string }
  | { type: 'event'; ev: AskEvent }
  | { type: 'reset' }

export function askReducer(state: AskState, action: AskAction): AskState {
  switch (action.type) {
    case 'start':
      return { ...initialState, phase: 'running', question: action.question }
    case 'reset':
      return initialState
    case 'event':
      return applyEvent(state, action.ev)
  }
}

function applyEvent(state: AskState, ev: AskEvent): AskState {
  switch (ev.event) {
    case 'node_done': {
      const e = ev as NodeDoneEvent
      // 同一节点可能一轮出现多次（重试/自愈），时间线保留全量——它就是排查询链
      return {
        ...state,
        timeline: [...state.timeline, { node: e.node, latency_ms: e.latency_ms, detail: e.detail }],
      }
    }
    case 'clarification':
      return { ...state, phase: 'clarifying', clarification: ev as ClarificationEvent }
    case 'approval':
      return { ...state, phase: 'approving', approval: ev as ApprovalEvent }
    case 'result':
      return { ...state, phase: 'done', result: ev as ResultEvent }
    case 'error':
      return { ...state, phase: 'error', error: ev.message }
    default:
      return state
  }
}
