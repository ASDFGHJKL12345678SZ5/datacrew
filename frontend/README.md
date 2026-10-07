# DataCrew 前端（Vue 3 问数控制台）

独立部署的 SPA，**对后端零侵入**：全部交互经既有 HTTP/SSE 契约完成
（`POST /ask` 问数、`POST /ask/resume` 恢复中断、`GET /files/...` 下载图表）。

## 运行

```powershell
# 前置：后端在 :8000（python -m app.main）
npm install
npm run dev        # http://localhost:5173
npm run build      # vue-tsc 类型检查 + vite build
npm test           # vitest：55 项单测（含 13 项组件/旅程测试，jsdom）
```

dev 模式经 Vite proxy 转发 `/ask` `/ask/resume` `/files` `/health` 到 :8000，
免 CORS；生产构建用 `VITE_API_BASE` 指向 API 网关。

## 它把后端契约用全了

| 后端能力 | 前端落点 |
|---|---|
| SSE 事件流（`event:` + `data:` 帧） | `src/services/sse.ts` 帧解析器（断帧/粘包/CRLF 全覆盖） |
| 澄清中断（interrupt） | `ClarificationCard`：选项按钮 + 自定义输入 → `resume(字符串)` |
| 审批中断（interrupt） | `ApprovalCard`：SQL 展示 + 批准/拒绝 → `resume({approved})` |
| `result` 事件 | `ResultCard`：结论 / SQL / 数据 / 图表四标签页（图表 Tab 按需懒加载） |
| `node_done` 进度 | `EventTimeline`：节点中文名 + 延迟 + attempt 序号（同节点多次 = 自愈重试） |
| `/files` 鉴权 | `ChartImage`：fetch + X-API-Key → blob URL，失败可重试 |
| X-API-Key / 429 限流 | `SessionBar` 运行时改 key（localStorage 持久化）；错误横幅透传后端文案 |

## 结构

```
src/
  types/events.ts     SSE 事件类型（与 ask_service 产出逐字段对齐）
  services/
    sse.ts            帧解析器（纯函数）
    reducer.ts        一次提问轮次的状态机（纯函数，可单测）
    timeline.ts       执行轨迹的展示规则：中文名/attempt/延迟人性化/detail 扁平化（纯函数）
    round.ts          轮次生命周期：AbortController + deadline 唯一所有者
    health.ts         连接健康状态机（probing/ok/unreachable，可注入探针）
    session.ts        会话 ID 单一事实源（localStorage + 订阅广播）
    api.ts            ask/resume 流 + 鉴权文件下载 + API_BASE 导出
  composables/
    useHealth.ts      App 级健康探测单例（侧栏灯与提问门控同源）
    useAsk.ts         reducer × api 接 Vue 响应式（只做接线）
  views/AskView.vue   页面编排
  components/
    base/             设计系统基件：BaseButton / BaseCard / Badge / Spinner / EmptyState
    ...               提问表单/会话栏/时间线/澄清卡/审批卡/结果卡/图表/表格
tests/                sse(8) reducer(6) round(6) health(4) health.reactive(1)
                      timeline(7) session(3) api.stream(6) components(9) app(2)
                      journey(2)（后八项 jsdom）
```

**设计纪律**：判断逻辑全在纯函数里（解析器/状态机/展示规则），组件只渲染；
纯函数有单测，UI 改动不碰协议。基件组件只消费 `style.css` 的 token，
不就地写颜色——换主题只动一份。

## 已修复的 bug（2026-10-03 实机复现，均有回归测试）

1. **SSE 流式管道死锁 → 结果被 30s 看门狗误报覆盖**：`postSse` 曾用
   wake/notify 做"跨块信号量"——notify 只在 `parser.feed` 时触发，feed 只在
   `reader.read` 之后，循环却在等 wake 才肯继续 read：第一个数据块的事件 yield
   完后生成器永久挂起，`run()` 的 for-await 永不退出 → 轮次永不 settle → 30s
   无事件看门狗把**已经显示的结果**打成了"连接 30 秒无新事件，已自动断开"。
   后端全链路毫秒级完成、代理逐字节正常，唯独浏览器侧收不了尾。
   修复：删掉信号量，改朴素 read 循环（解析器是同步的，块间靠 read() 天然同步）。
   回归：`tests/api.stream.test.ts`（多分片流 + 生成器必须收敛 + 半帧尾巴）。
   **教训：程序化压测（Node fetch 直连/经代理）全绿≠浏览器路径正确**——
   这次是两个 bug 都是只在真实浏览器旅程里现形的。
2. **健康横幅冻结**：`useHealth()` 的 `isDown` 曾是普通 getter，被
   `const { healthDown } = useAsk()` 解构的那一刻值就固化——页面在后端不可达时
   打开，之后后端恢复了，横幅永不消、提问按钮永远禁用（实机被 HMR 刷新掩盖）。
   修复：改 `computed`，解构传递 Ref 本体、模板自动解包，判定始终来自响应式源。
   回归：`tests/journey.test.ts`（不可达→恢复→横幅消失）。

## 健壮性约定

- `strictPort: true`——端口被占时启动直接失败，绝不静默换端口（曾因静默换端口误连到另一个实例）。
- 流僵死看门狗——SSE 连接 30s 无字节即主动断开并给出明确错误；流正常结束但状态机仍停在 running 时按错误收尾，不会永远转圈。
- 取消按钮经 AbortController 真正掐断请求；App 外壳与提问门控共用 `useHealth()`
  单例探测 `/health`（15s 周期、6s 超时），后端恢复后状态灯自动变绿，无需刷新。
- API Key 与 session ID 均持久化在 localStorage，刷新不丢；改 key 不用重新构建。
