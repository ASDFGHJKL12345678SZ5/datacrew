"""DataCrew 问数 Demo（Streamlit）。

对着 API 的 SSE 流做前端渲染，覆盖两类人机交互：
- 澄清（clarification）：口径歧义时 Agent 追问，用户回答后 /ask/resume 恢复
- 审批（approval）：大表无过滤查询被安全闸挂起，人工批准/拒绝后恢复

设计说明：
- 事件流是"扁平 dict"（event/node/latency_ms/detail），没有 data 包装层；
  每条 SSE 行以 "data: " 开头，直接 json.loads 即可
- 中断后 session_state 记住待回答的 payload，Streamlit  rerun 不丢状态
- session_id 可编辑：同一会话的状态在 checkpointer 里跨请求延续，
  改 session_id 就是开新会话
- 示例问题按 mock 模式的行为挑选（见 app/agents/llm_mock.py 的意图表）：
  真实模型下的触发路径不同，问题本身仍然有效
"""
import json

import httpx
import streamlit as st

st.set_page_config(page_title="DataCrew 问数 Demo", page_icon="📊", layout="wide")

# ---------------- 侧边栏配置 ----------------
with st.sidebar:
    st.header("连接配置")
    api_base = st.text_input("API 地址", value="http://127.0.0.1:8000")
    api_key = st.text_input("API Key", value="dev-key-001")
    session_id = st.text_input("会话 ID", value="demo-session-1")
    st.caption("同一会话 ID 的状态跨请求延续；换 ID 即开新会话")
    st.divider()
    st.caption("LLM_MODE=mock 下：规则假模型，用于无 Key 演示交互链路")

# ---------------- 会话状态 ----------------
if "pending" not in st.session_state:
    st.session_state.pending = None  # 待回答的中断 payload
if "trace" not in st.session_state:
    st.session_state.trace = []      # 节点执行轨迹（跨 rerun 保留）


def consume_sse(resp: httpx.Response) -> list[dict]:
    """把 SSE 响应解析成事件列表。每行 'data: {json}'。"""
    events = []
    for line in resp.iter_lines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[6:]))
    return events


def render_events(events: list[dict]) -> None:
    """渲染一批事件：节点轨迹实时追加，中断/结果/错误分别落盘。"""
    for ev in events:
        kind = ev.get("event")
        if kind == "node_done":
            st.session_state.trace.append(ev)
        elif kind == "result":
            st.session_state.result = ev
        elif kind == "error":
            st.session_state.result = ev
        elif kind in ("clarification", "approval"):
            st.session_state.pending = ev
        else:
            st.session_state.trace.append(ev)


def run_ask(question: str) -> None:
    """POST /ask，消费 SSE 流。"""
    with httpx.Client(timeout=60.0) as client:
        with client.stream(
            "POST",
            f"{api_base}/ask",
            json={"question": question, "session_id": session_id},
            headers={"X-API-Key": api_key},
        ) as resp:
            if resp.status_code != 200:
                st.error(f"API 返回 {resp.status_code}: {resp.read().decode()[:200]}")
                return
            render_events(consume_sse(resp))


def run_resume(value) -> None:
    """POST /ask/resume，value 是澄清回答（str）或审批决定（dict）。"""
    st.session_state.pending = None
    with httpx.Client(timeout=60.0) as client:
        with client.stream(
            "POST",
            f"{api_base}/ask/resume",
            json={"session_id": session_id, "value": value},
            headers={"X-API-Key": api_key},
        ) as resp:
            if resp.status_code != 200:
                st.error(f"API 返回 {resp.status_code}: {resp.read().decode()[:200]}")
                return
            render_events(consume_sse(resp))


# ---------------- 主界面 ----------------
st.title("📊 DataCrew 电商智能问数")
st.caption("LangGraph 多智能体：SchemaCurator → SqlGenerator → Executor → InsightWriter")

examples = [
    ("📈 普通问数", "上个月有多少下单用户"),
    ("❓ 触发澄清", "销售额是多少"),
    ("⚠️ 触发审批", "把所有订单的详细信息都列出来"),
]
cols = st.columns(3)
for col, (label, q) in zip(cols, examples, strict=True):
    if col.button(label):
        st.session_state.trace = []
        st.session_state.result = None
        run_ask(q)

question = st.chat_input("输入你的数据问题…")
if question:
    st.session_state.trace = []
    st.session_state.result = None
    run_ask(question)

# 节点轨迹
if st.session_state.trace:
    st.subheader("执行轨迹")
    for ev in st.session_state.trace:
        node = ev.get("node", "?")
        latency = ev.get("latency_ms")
        detail = ev.get("detail") or {}
        with st.container(border=True):
            head = f"**{node}**" + (f" · {latency}ms" if latency is not None else "")
            st.markdown(head)
            interesting = {k: v for k, v in detail.items() if k in (
                "sql", "error", "approval", "clarified", "row_count", "refused", "reason")}
            for k, v in interesting.items():
                if k == "sql":
                    st.code(str(v), language="sql")
                else:
                    st.text(f"{k}: {v}")

# 人机交互：澄清 / 审批
if st.session_state.pending:
    ev = st.session_state.pending
    if ev.get("event") == "clarification":
        st.warning(f"🤔 Agent 需要澄清：{ev.get('question', '')}")
        options = ev.get("options") or []
        if options:
            choice = st.radio("选择一个口径", options)
            if st.button("提交回答", type="primary"):
                run_resume(choice)
        else:
            answer = st.text_input("你的回答")
            if st.button("提交回答", type="primary") and answer:
                run_resume(answer)
    elif ev.get("event") == "approval":
        st.warning(f"⚠️ 该查询需要人工审批：{ev.get('reason', '')}")
        st.code(ev.get("sql", ""), language="sql")
        c1, c2 = st.columns(2)
        if c1.button("✅ 批准执行", type="primary"):
            run_resume({"approved": True})
        if c2.button("❌ 拒绝"):
            run_resume({"approved": False})

# 最终结果
if st.session_state.get("result"):
    res = st.session_state.result
    if res.get("event") == "error":
        st.error(f"查询失败：{res.get('message', '')}")
    else:
        st.subheader("结论")
        st.markdown(res.get("summary") or "（无摘要）")
        if res.get("sql"):
            with st.expander("查看 SQL"):
                st.code(res["sql"], language="sql")
        # 图表 URL 是 API 端路径（/files/...），用 API 地址拼全；
        # 直接相对引用会解析到 Streamlit 自己的端口导致裂图
        if res.get("chart_url"):
            st.image(api_base + res["chart_url"], caption="查询结果图表")
        rows = res.get("rows") or []
        if rows:
            st.dataframe(rows, use_container_width=True)
            n = res.get("row_count", len(rows))
            retried = res.get("retry_count", 0)
            st.caption(f"共 {n} 行 · 重试 {retried} 次")
