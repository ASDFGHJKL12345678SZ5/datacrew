"""图表生成：把查询结果渲染成 SVG 图表，落对象存储返回 URL。

为什么手写 SVG 而不用 matplotlib（ADR，面试可讲）：
    1. 零依赖：镜像不增大 100MB+，CI/离线环境直接可跑
    2. 确定性输出：同一数据永远同一 SVG——评测与回归可比对
       （matplotlib 的默认样式/字体随版本漂移，截图对比是噩梦）
    3. SVG 是文本：前端 <img src> 直接用、可内嵌、可 diff
    4. 数据本来就小：问数结果的图表是"前 10 行聚合值"，
       不是仪表盘——为 10 根柱子引入一个渲染引擎是过度工程

支持 bar / line / pie 三类，输入对齐 sql_execute 的结果结构
（columns + rows），也接受 [{label, value}] 形态。
"""
from __future__ import annotations

from app.core.logging import get_logger

log = get_logger(__name__)

SUPPORTED_TYPES = ("bar", "line", "pie")
MAX_POINTS = 20          # 超过 20 个数据点的图表没人看得懂，调用方应预聚合
CANVAS_W, CANVAS_H = 640, 360
PADDING = 56

_PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
            "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"]


class ChartError(Exception):
    """图表生成失败（回灌给 Agent 驱动自愈）。"""


def _esc(text: str) -> str:
    """XML 实体转义：标签来自查询结果（可能是用户数据），必须转义。"""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _norm_points(data: list) -> list[tuple[str, float]]:
    """把输入归一成 [(label, value)]。空/非数值/超量都抛 ChartError。"""
    if not isinstance(data, list) or not data:
        raise ChartError("数据为空：data 需要是非空列表")
    points: list[tuple[str, float]] = []
    for row in data:
        if isinstance(row, dict):
            label, value = row.get("label"), row.get("value")
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            label, value = row[0], row[1]
        else:
            # 严格两列：多列静默丢弃会让"该做成散点的数据"画成错误图表，
            # 不如明确报错让 Agent 自愈（错误信息即修复指令）
            raise ChartError(
                f"无法解析的数据行: {row!r}（期望恰好两列 [label, value] 或 {{'label','value'}}）"
            )
        try:
            value = float(value)
        except (TypeError, ValueError) as e:
            raise ChartError(f"数值无法解析: {value!r}") from e
        points.append((str(label), value))
    if len(points) > MAX_POINTS:
        raise ChartError(
            f"数据点 {len(points)} 个超过上限 {MAX_POINTS}——请先在 SQL 里聚合/取 TopN"
        )
    return points


def _frame(title: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" '
        f'height="{CANVAS_H}" viewBox="0 0 {CANVAS_W} {CANVAS_H}" '
        f'font-family="sans-serif">'
        f'<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<text x="{CANVAS_W // 2}" y="28" text-anchor="middle" '
        f'font-size="16" font-weight="bold">{_esc(title)}</text>'
        f"{body}</svg>"
    )


def _bar_svg(title: str, points: list[tuple[str, float]]) -> str:
    plot_w = CANVAS_W - 2 * PADDING
    plot_h = CANVAS_H - PADDING - 60
    lo = min(0, min(v for _, v in points))
    hi = max(v for _, v in points)
    span = (hi - lo) or 1.0
    n = len(points)
    slot = plot_w / n
    bar_w = max(6, slot * 0.6)
    body = [f'<line x1="{PADDING}" y1="{CANVAS_H - 60}" x2="{CANVAS_W - PADDING}" '
            f'y2="{CANVAS_H - 60}" stroke="#333"/>']
    for i, (label, value) in enumerate(points):
        x = PADDING + i * slot + (slot - bar_w) / 2
        h = abs(value - lo) / span * plot_h
        y = CANVAS_H - 60 - h
        color = _PALETTE[i % len(_PALETTE)]
        body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                    f'height="{h:.1f}" fill="{color}"/>')
        body.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 6:.1f}" '
                    f'text-anchor="middle" font-size="11">{_esc(round(value, 2))}</text>')
        body.append(f'<text x="{x + bar_w / 2:.1f}" y="{CANVAS_H - 42}" '
                    f'text-anchor="middle" font-size="11">{_esc(label[:8])}</text>')
    return _frame(title, "".join(body))


def _line_svg(title: str, points: list[tuple[str, float]]) -> str:
    plot_w = CANVAS_W - 2 * PADDING
    plot_h = CANVAS_H - PADDING - 60
    lo, hi = min(v for _, v in points), max(v for _, v in points)
    span = (hi - lo) or 1.0
    n = len(points)
    step = plot_w / max(n - 1, 1)
    coords = [
        (PADDING + i * step, CANVAS_H - 60 - (v - lo) / span * plot_h)
        for i, (_, v) in enumerate(points)
    ]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    body = [f'<polyline points="{path}" fill="none" stroke="#4e79a7" stroke-width="2"/>']
    for (x, y), (label, value) in zip(coords, points, strict=True):
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#e15759"/>')
        body.append(f'<text x="{x:.1f}" y="{y - 8:.1f}" text-anchor="middle" '
                    f'font-size="10">{_esc(round(value, 2))}</text>')
        body.append(f'<text x="{x:.1f}" y="{CANVAS_H - 42}" text-anchor="middle" '
                    f'font-size="10">{_esc(label[:8])}</text>')
    return _frame(title, "".join(body))


def _pie_svg(title: str, points: list[tuple[str, float]]) -> str:
    import math

    cx, cy, r = CANVAS_W // 2, (CANVAS_H + 30) // 2, 110
    total = sum(v for _, v in points)
    if total <= 0:
        raise ChartError("饼图要求正值合计 > 0")
    body: list[str] = []
    angle = -math.pi / 2
    for i, (label, value) in enumerate(points):
        share = value / total
        end = angle + share * 2 * math.pi
        large = 1 if share > 0.5 else 0
        x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
        color = _PALETTE[i % len(_PALETTE)]
        body.append(
            f'<path d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {r} {r} 0 {large} 1 '
            f'{x2:.1f} {y2:.1f} Z" fill="{color}" stroke="#fff"/>'
        )
        mid = (angle + end) / 2
        lx, ly = cx + (r + 18) * math.cos(mid), cy + (r + 18) * math.sin(mid)
        body.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="11">'
                    f"{_esc(label[:6])}</text>")
        angle = end
    return _frame(title, "".join(body))


def render_svg(chart_type: str, data: list, title: str = "") -> str:
    """渲染 SVG 字符串。chart_type: bar | line | pie。"""
    chart_type = (chart_type or "bar").lower()
    if chart_type not in SUPPORTED_TYPES:
        raise ChartError(f"不支持的图表类型: {chart_type}（支持 {SUPPORTED_TYPES}）")
    points = _norm_points(data)
    title = title or f"{chart_type} chart"
    if chart_type == "bar":
        return _bar_svg(title, points)
    if chart_type == "line":
        return _line_svg(title, points)
    return _pie_svg(title, points)


async def generate_chart(chart_type: str, data: list, title: str = "") -> dict:
    """生成图表并落对象存储，返回 {"ok", "url", "key", "points"} / 错误结构。"""
    import time

    from app.infra.storage import get_storage

    started = time.perf_counter()
    try:
        svg = render_svg(chart_type, data, title)
    except ChartError as e:
        log.warning("chart.rejected", extra={"context": {"reason": str(e)}})
        return {"ok": False, "error": str(e), "error_type": "bad_request"}
    key = f"charts/{int(time.time() * 1000)}-{chart_type}.svg"
    try:
        url = get_storage().put(key, svg.encode("utf-8"), "image/svg+xml")
    except Exception as e:  # 存储故障不该炸掉整条问数链路
        log.exception("chart.storage_failed")
        return {"ok": False, "error": f"图表存储失败: {e}", "error_type": "storage"}
    log.info(
        "chart.generated",
        extra={"context": {"key": key, "latency_ms": int((time.perf_counter() - started) * 1000)}},
    )
    return {
        "ok": True,
        "url": url,
        "key": key,
        "points": len(data),
        "format": "svg",
        "error_type": "ok",
    }
