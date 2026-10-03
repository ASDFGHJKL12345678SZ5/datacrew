// DataCrew SSE 事件契约（与 app/application/ask_service.py 的产出逐字段对齐）
// 帧格式：event: <类型>\n data: <json>\n\n（注意 finrag 的帧没有 event: 头）

export interface NodeDoneEvent {
  event: 'node_done'
  node: string
  latency_ms?: number
  detail?: Record<string, unknown>
}

export interface ClarificationEvent {
  event: 'clarification'
  type: 'clarification'
  question: string
  options: string[]
}

export interface ApprovalEvent {
  event: 'approval'
  type: 'approval'
  reason: string
  sql: string
}

export interface ResultEvent {
  event: 'result'
  summary: string
  sql: string | null
  columns: string[] | null
  rows: unknown[][] | null
  row_count?: number
  chart_url?: string | null
  retry_count?: number
}

export interface ErrorEvent {
  event: 'error'
  message: string
}

export type AskEvent =
  | NodeDoneEvent
  | ClarificationEvent
  | ApprovalEvent
  | ResultEvent
  | ErrorEvent

export type EventType = AskEvent['event']
