# DataCrew 前端（Vue 3 问数控制台）

独立部署的 SPA，**对后端零侵入**：全部交互经既有 HTTP/SSE 契约完成
（`POST /ask` 问数、`POST /ask/resume` 恢复中断、`GET /files/...` 下载图表）。

## 运行

```powershell
# 前置：后端在 :8000（python -m app.main）
npm install
npm run dev        # http://localhost:5173
npm run build      # vue-tsc 类型检查 + vite build
npm test           # vitest：14 项单测
```

dev 模式经 Vite proxy 转发 `/ask` `/ask/resume` `/files` `/health` 到 :8000，
免 CORS；生产构建用 `VITE_API_BASE` 指向 API 网关。

## 它把后端契约用全了

| 后端能力 | 前端落点 |
|---|---|
| SSE 事件流（`event:` + `data:` 帧） | `src/services/sse.ts` 帧解析器（断帧/粘包/CRLF 全覆盖） |
| 澄清中断（interrupt） | `ClarificationCard`：选项按钮 + 自定义输入 → `resume(字符串)` |
| 审批中断（interrupt） | `ApprovalCard`：SQL 展示 + 批准/拒绝 → `resume({approved})` |
| `result` 事件 | `ResultCard`：Markdown 洞察（marked+DOMPurify）+ SQL + 行数 + 图表 |
| `/files` 鉴权 | `ChartImage`：fetch + X-API-Key → blob URL（`<img>` 带不了 header） |
| node_done 进度 | `EventTimeline`：executor 多次出现 = 自愈重试，一眼可见 |
| X-API-Key / 429 限流 | `SessionBar` 运行时改 key；错误横幅透传后端文案 |

## 结构

```
src/
  types/events.ts    SSE 事件类型（与 ask_service 产出逐字段对齐）
  services/sse.ts    帧解析器（纯函数）
  services/reducer.ts 一次提问轮次的状态机（纯函数，可单测）
  services/api.ts     ask/resume 流 + 鉴权文件下载
  composables/useAsk.ts reducer × api 接 Vue 响应式（只做接线）
  views/AskView.vue  页面编排
  components/         提问表单/会话栏/时间线/澄清卡/审批卡/结果卡/图表
tests/                sse.test.ts（8）+ reducer.test.ts（6）
```

**设计纪律**：判断逻辑全在纯函数里（解析器/状态机），组件只渲染；
纯函数有单测，UI 改动不碰协议。

## 健壮性约定

- `strictPort: true`——端口被占时启动直接失败，绝不静默换端口（曾因静默换端口误连到另一个实例）。
- 流僵死看门狗——SSE 连接 60s 无字节即主动断开并给出明确错误；流正常结束但状态机仍停在 running 时按错误收尾，不会永远转圈。
- 取消按钮经 AbortController 真正掐断请求；App 外壳每 15s 探测 `/health`，后端恢复后状态灯自动变绿，无需刷新。
