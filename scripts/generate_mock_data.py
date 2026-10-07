#!/usr/bin/env python
"""Mock 电商数据生成器：合成贴近真实的企业级电商库。

设计目标（面试可讲）：
1. 规模真实：5w 用户 / 50w 订单 / 200w 订单明细 —— 让"查询性能"成为真实问题
2. 分布真实：帕累托集中（头部用户贡献多数订单）、渠道差异、大促尖峰（双11/618）
3. 埋点可控：三类"脏点"是刻意注入的评测素材（对应 schema 注释）
     埋点A 口径歧义：orders.gmv_amount vs pay_amount
     埋点B 脏值    ：products.status 含 NULL / 2(预售)
     埋点C 时间陷阱：orders.created_at(下单) vs pay_time(支付，未支付为 NULL)
4. 引用完整：所有外键真实有效 —— "脏"是业务语义层面的，不是数据库层面的
5. 可复现：固定随机种子，任何人任何机器跑出的数据完全一致

用法：
    python scripts/generate_mock_data.py              # 默认规模并验证
    python scripts/generate_mock_data.py --scale 0.1  # 1/10 规模（快速验证用）
    python scripts/generate_mock_data.py --verify     # 只跑数据质量检查，不生成
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import redis.asyncio as aioredis

# ---------------------------------------------------------------- 基础配置
SEED = 42
NOW = datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)  # 数据截止时间
HISTORY_DAYS = 365
# DSN 默认 5432（与 docker compose / CI service 一致），但允许 PG_ADMIN_DSN 覆盖——
# 曾因硬编码导致本地复现 CI 时写错库（真实事故），切库必须显式可配。
ADMIN_DSN = os.environ.get("PG_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5432/ecommerce")

SCALES = {  # (默认条数, 每批条数)
    "users": (50_000, 50_000),
    "products": (200, 200),
    "orders": (500_000, 50_000),
    "traffic": (200_000, 50_000),
}

CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京", "苏州", "长沙", "重庆"]
CHANNELS = ["app", "miniapp", "h5"]
CHANNEL_W = [0.55, 0.35, 0.10]

CATEGORIES = {
    "手机数码": ["华为", "小米", "苹果", "OPPO", "vivo", "荣耀"],
    "家用电器": ["美的", "格力", "海尔", "松下", "飞利浦"],
    "服装鞋包": ["优衣库", "海澜之家", "安踏", "李宁", "太平鸟"],
    "美妆个护": ["珀莱雅", "薇诺娜", "欧莱雅", "兰蔻"],
    "食品生鲜": ["三只松鼠", "百草味", "蒙牛", "伊利"],
    "家居家装": ["宜家", "源氏木语", "喜临门"],
    "运动户外": ["迪卡侬", "骆驼", "始祖鸟"],
    "母婴亲子": ["babycare", "好孩子", "贝亲"],
}
ORDER_STATUS_W = [("created", 0.05), ("paid", 0.85), ("cancelled", 0.05), ("refunded", 0.05)]
TRAFFIC_EVENTS = ["view", "click", "cart", "order"]
TRAFFIC_W = [0.60, 0.25, 0.10, 0.05]


# ============================================================================
# 指标口径注册表（唯一事实源：seeder 与 scripts/sync_metric_definitions.py 共用）
# ============================================================================
METRIC_DEFINITIONS: list[tuple[str, str, str, str]] = [
    # ---- 整体类（单一标量）----
    ("GMV", "所有下单订单的成交总额（含取消/退款单），取 orders.gmv_amount 求和，不过滤 order_status",
     "SELECT SUM(gmv_amount) FROM biz.orders WHERE created_at >= $1 AND created_at < $2",
     "整体类：返回单一标量"),
    ("实付销售额", "支付成功的订单实付金额总和，取 orders.pay_amount 求和，order_status IN ('paid','refunded')",
     "SELECT SUM(pay_amount) FROM biz.orders WHERE order_status IN ('paid','refunded') AND pay_time >= $1 AND pay_time < $2",
     "整体类：返回单一标量；可按渠道/城市/品类/时间分组（即成分组类）"),
    ("客单价", "实付销售额 / 支付成功订单数",
     "SELECT SUM(pay_amount)/NULLIF(COUNT(*),0) FROM biz.orders WHERE order_status IN ('paid','refunded')",
     "整体类：单一标量"),
    ("下单用户数", "统计周期内产生订单的去重用户数",
     "SELECT COUNT(DISTINCT user_id) FROM biz.orders WHERE created_at >= $1 AND created_at < $2",
     "整体类：单一标量"),
    ("订单量", "订单数 COUNT(*)，含全部 order_status（=下单数，不存在是否只算支付的追问）",
     "SELECT COUNT(*) FROM biz.orders WHERE created_at >= $1 AND created_at < $2",
     "分组类：可按城市/渠道/品类/时间多维分组"),
    ("销量", "商品件数合计 SUM(order_items.quantity)",
     "SELECT SUM(i.quantity) FROM biz.order_items i JOIN biz.orders o ON o.id=i.order_id WHERE o.created_at >= $1 AND o.created_at < $2",
     "分组类：可按品类/商品/时间分组"),
    ("购买用户数", "按维度分组的去重下单用户数 COUNT(DISTINCT orders.user_id)；按品类分组需 JOIN order_items+products",
     "SELECT p.category, COUNT(DISTINCT o.user_id) FROM biz.orders o JOIN biz.order_items i ON o.id=i.order_id JOIN biz.products p ON i.product_id=p.id GROUP BY p.category",
     "分组类：分子是去重用户数（不是订单数）"),
    ("件单价", "成交单价的平均值 AVG(order_items.price)（**不是 SUM(price)/SUM(quantity)**）",
     "SELECT p.category, AVG(i.price) FROM biz.order_items i JOIN biz.products p ON i.product_id=p.id GROUP BY p.category",
     "分组类/排名类：按品类分组后按件单价降序取 TopN"),
    ("连带率", "品类平均每单件数 = 该品类销量总件数 / 该品类所在订单数（统计全部订单，含取消/退款单，不额外过滤 order_status）",
     "SELECT p.category, SUM(i.quantity)::numeric / NULLIF(COUNT(DISTINCT i.order_id),0) FROM biz.order_items i JOIN biz.products p ON p.id=i.product_id GROUP BY p.category",
     "分组类：分母是该品类所在订单数（COUNT DISTINCT order_id）"),
    ("日均订单量", "统计周期订单总数 / 天数",
     "SELECT COUNT(*)::numeric / GREATEST(EXTRACT(EPOCH FROM ($2::timestamptz - $1::timestamptz))/86400, 1) FROM biz.orders WHERE created_at >= $1 AND created_at < $2",
     "整体类：单一标量"),
    ("新用户数", "首次下单时间落在统计周期内的用户数",
     "SELECT COUNT(*) FROM (SELECT user_id, MIN(created_at) first_order FROM biz.orders GROUP BY user_id) t WHERE first_order >= $1 AND first_order < $2",
     "整体类：单一标量"),
    ("老用户", "统计周期开始**之前**已下过单的用户（用于'老用户复购率'等）",
     "SELECT DISTINCT user_id FROM biz.orders WHERE created_at < $1",
     "整体类：作为子查询的用户集合使用"),
    ("日活用户", "当日有任意行为（浏览/点击/加购/下单）的去重用户数，数据源 traffic_logs",
     "SELECT COUNT(DISTINCT user_id) FROM biz.traffic_logs WHERE ts >= $1 AND ts < $2 AND user_id IS NOT NULL",
     "整体类：单一标量"),
    # ---- 占比类（本组值 / 全表合计 × 100，两位小数）----
    ("渠道销售额占比", "各渠道实付销售额 / 全部渠道实付销售额合计（以百分比表示，保留两位小数）",
     "SELECT channel, ROUND(100.0 * SUM(pay_amount) / (SELECT SUM(pay_amount) FROM biz.orders WHERE order_status IN ('paid','refunded')), 2) FROM biz.orders WHERE order_status IN ('paid','refunded') GROUP BY channel",
     "占比类：分母=全表合计（标量子查询，不带分组条件）"),
    ("维度销售额占比", "某一维度的实付销售额 / 全部实付销售额合计（以百分比表示，保留两位小数）",
     "SELECT p.category, ROUND(100.0 * SUM(o.pay_amount) / (SELECT SUM(pay_amount) FROM biz.orders WHERE order_status IN ('paid','refunded')), 2) FROM biz.orders o JOIN biz.order_items i ON o.id=i.order_id JOIN biz.products p ON i.product_id=p.id WHERE o.order_status IN ('paid','refunded') GROUP BY p.category",
     "占比类：分母是**全表**合计，不是本组；按品类/城市/渠道分组均可"),
    ("TopN 贡献占比", "按用户实付金额降序取前 N 名，这 N 名的合计 / 全部用户实付合计（百分比、两位小数）",
     "SELECT ROUND(100.0 * (SELECT SUM(pay) FROM (SELECT SUM(pay_amount) pay FROM biz.orders WHERE order_status IN ('paid','refunded') GROUP BY user_id ORDER BY pay DESC LIMIT $1) t) / (SELECT SUM(pay_amount) FROM biz.orders WHERE order_status IN ('paid','refunded')), 2)",
     "占比类：分子是 TopN 子查询的合计"),
    ("未支付占比", "未支付订单（order_status='created'）数 / 全部订单数（百分比、两位小数）",
     "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE order_status='created') / NULLIF(COUNT(*),0), 2) FROM biz.orders",
     "占比类：可加 GROUP BY channel 等维度；分母是全部订单"),
    # ---- 排名类（排序 + TopN）----
    ("商品销量TopN", "按销量（SUM(order_items.quantity)）降序的商品前 N 名",
     "SELECT i.product_id, SUM(i.quantity) qty FROM biz.order_items i JOIN biz.orders o ON o.id=i.order_id WHERE o.order_status IN ('paid','refunded') GROUP BY i.product_id ORDER BY qty DESC LIMIT $1",
     "排名类：默认销量降序；可加品类等筛选条件"),
    ("下单金额TopN用户", "按用户实付金额（SUM(orders.pay_amount)，**实付口径不是 GMV**）降序的前 N 个用户",
     "SELECT user_id, SUM(pay_amount) total FROM biz.orders WHERE order_status IN ('paid','refunded') GROUP BY user_id ORDER BY total DESC LIMIT $1",
     "排名类：**下单金额 = 实付金额**（反直觉，必须按此口径）"),
    # ---- 人均类（先聚合再平均）----
    ("人均订单金额", "该维度订单的 AVG(orders.pay_amount)（**订单级平均**：先按维度筛选订单再取平均；不是'每个用户先汇总再平均'）",
     "SELECT AVG(o.pay_amount) FROM biz.orders o JOIN biz.order_items i ON o.id=i.order_id JOIN biz.products p ON i.product_id=p.id WHERE p.category = $1 AND o.order_status IN ('paid','refunded')",
     "人均类：**对订单取平均**（反直觉，别先按用户汇总）"),
    ("平均下单频次", "先按用户聚合下单次数 COUNT(*)，再对所有用户取 AVG",
     "SELECT AVG(cnt) FROM (SELECT o.user_id, COUNT(*) cnt FROM biz.orders o JOIN biz.users u ON o.user_id=u.id WHERE u.channel_source = $1 GROUP BY o.user_id) t",
     "人均类：子查询先聚合到用户，再 AVG"),
    # ---- 转化类（人数或订单数的比率/交集）----
    ("复购率", "周期内下单 2 次及以上的用户数 / 有下单的用户数（以百分比表示，保留两位小数）",
     "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE cnt >= 2)::numeric / NULLIF(COUNT(*),0), 2) FROM (SELECT user_id, COUNT(*) cnt FROM biz.orders WHERE created_at >= $1 AND created_at < $2 GROUP BY user_id) t",
     "转化类：分母是有下单的用户数；可加'老用户'等用户集合条件"),
    ("退款率", "退款订单数 / 支付成功订单数（分母是支付成功单，不是全部订单；以百分比表示，保留两位小数；可按渠道/时间分组，分组后按值排序即得退款率最高的渠道）",
     "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE order_status='refunded')::numeric / NULLIF(COUNT(*) FILTER (WHERE order_status IN ('paid','refunded')),0), 2) FROM biz.orders",
     "转化类：**分母是支付成功单**（反直觉，别用全部订单）；可分组"),
    ("下单转化率", "下单用户数 / 活跃用户数（traffic_logs 去重；以百分比表示，保留两位小数）",
     "SELECT ROUND(100.0 * COUNT(DISTINCT o.user_id)::numeric / NULLIF(COUNT(DISTINCT t.user_id),0), 2) FROM biz.orders o FULL JOIN biz.traffic_logs t ON o.user_id=t.user_id WHERE o.created_at >= $1 AND o.created_at < $2",
     "转化类：分子=下单用户数、分母=活跃用户数（都是去重人数）"),
    ("取消率", "取消订单数 / **全部订单数**（注意与退款率不同：退款率的分母是支付成功单；以百分比表示，保留两位小数）",
     "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE order_status='cancelled')::numeric / NULLIF(COUNT(*),0), 2) FROM biz.orders",
     "转化类：分母是**全部订单**（与退款率不同），可加维度分组"),
    ("下单到支付转化率", "支付成功订单数（paid/refunded）/ 全部下单数（百分比、两位小数）",
     "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE order_status IN ('paid','refunded'))::numeric / NULLIF(COUNT(*),0), 2) FROM biz.orders",
     "转化类：分子分母都是**订单数**（不是用户数）"),
    ("加购到购买转化用户数", "既是加购用户（traffic_logs.event_type='cart'）又有下单记录的去重用户数（**是人数，不是比率**）",
     "SELECT COUNT(DISTINCT t.user_id) FROM biz.traffic_logs t WHERE t.event_type='cart' AND EXISTS (SELECT 1 FROM biz.orders o WHERE o.user_id=t.user_id)",
     "转化类：输出**人数**（不是百分比）；交集语义用 EXISTS"),
]


def weighted_choice(rng: random.Random, pairs: list[tuple[str, float]]) -> str:
    r = rng.random()
    acc = 0.0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


def sample_created_at(rng: random.Random) -> datetime:
    """下单时间：过去 365 天，双11/618 尖峰，晚间高峰。"""
    # 日期权重：普通日 1，大促日加权
    promo = {"11-11": 15.0, "06-18": 8.0, "12-12": 4.0, "10-01": 5.0}
    weights = []
    days = []
    for d in range(HISTORY_DAYS):
        dt = NOW - timedelta(days=d)
        key = dt.strftime("%m-%d")
        days.append(dt)
        weights.append(promo.get(key, 1.0))
    dt = rng.choices(days, weights=weights, k=1)[0]
    # 小时分布：晚间 20-22 点高峰
    hours = list(range(24))
    hour_w = [1, 0.6, 0.4, 0.3, 0.3, 0.5, 1, 2, 2.5, 3, 3.2, 3.5, 3, 3, 3, 3.2, 3.5, 4, 5, 7, 9, 10, 6, 3]
    h = rng.choices(hours, weights=hour_w, k=1)[0]
    return dt.replace(hour=h, minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0)


def gen_users(rng: random.Random, n: int) -> list[tuple]:
    surnames = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
    givens = list("伟芳娜秀敏静丽强磊洋勇军杰娟涛明超秀霞平刚桂英文华建国国强军芳芳伟伟丽丽")
    rows = []
    for _i in range(n):
        name = rng.choice(surnames) + "".join(rng.choices(givens, k=rng.choice([1, 2])))
        channel = rng.choices(CHANNELS, weights=CHANNEL_W, k=1)[0]
        rows.append((name, rng.choice(CITIES), channel, sample_created_at(rng)))
    return rows


def gen_products(rng: random.Random, n: int) -> list[tuple]:
    rows = []
    for i in range(1, n + 1):
        cat = rng.choice(list(CATEGORIES))
        brand = rng.choice(CATEGORIES[cat])
        name = f"{brand} {cat}{rng.randint(100, 999)}号"
        # 埋点B：前 4 个商品确定性注入脏值（保证任何规模下评测素材都存在），
        # 其余随机：1上架80% / 0下架10% / 2预售5% / NULL 5%
        if i <= 2:
            status = None
        elif i <= 4:
            status = 2
        else:
            r = rng.random()
            status = None if r < 0.05 else (2 if r < 0.10 else (0 if r < 0.20 else 1))
        price = Decimal(str(round(rng.lognormvariate(6.0, 1.0), 2))).quantize(Decimal("0.01"))
        price = min(max(price, Decimal("9.90")), Decimal("29999.00"))
        rows.append((name, cat, status, price))
    return rows


def gen_orders_and_items(rng: random.Random, id_start: int, count: int,
                         n_users: int, products: list[tuple]):
    """订单 + 明细一起生成，保证金额一致（gmv_amount == SUM(price*quantity)）。

    id_start：本批次首个订单的 ID（必须与 COPY 后数据库自增 ID 对齐，
              否则 order_items.order_id 会指向错误订单 —— 外键孤儿 bug）。
    埋点A：gmv_amount 与 pay_amount 语义不同（GMV 含取消/退款，实付扣折扣）
    埋点C：created_at 与 pay_time 语义不同（未支付订单 pay_time 为 NULL）
    """
    orders: list[tuple] = []
    items: list[tuple] = []
    for oid in range(id_start, id_start + count):
        # 集中度模型：80% 订单来自活跃用户池（前 20% 注册用户），20% 均匀撒向全体
        # —— 近似真实电商的头部集中（Top100 用户约 8% 订单量级，而非 99%）
        if rng.random() < 0.8:
            uid = rng.randint(1, max(1, int(n_users * 0.2)))
        else:
            uid = rng.randint(1, n_users)
        status = weighted_choice(rng, ORDER_STATUS_W)
        channel = rng.choices(CHANNELS, weights=CHANNEL_W, k=1)[0]
        created = sample_created_at(rng)

        # 明细：1~5 件
        n_items = rng.choices([1, 2, 3, 4, 5], weights=[0.70, 0.18, 0.08, 0.03, 0.01], k=1)[0]
        chosen = rng.sample(products, k=min(n_items, len(products)))
        gmv = Decimal("0")
        order_items: list[tuple] = []
        for (pid, _name, _cat, _status, list_price) in chosen:
            qty = rng.choices([1, 2, 3, 4, 5], weights=[0.70, 0.18, 0.08, 0.03, 0.01], k=1)[0]
            price = (list_price * Decimal(str(rng.uniform(0.7, 1.0)))).quantize(Decimal("0.01"))
            gmv += price * qty
            order_items.append((oid, pid, qty, price))

        # 埋点A：实付 = GMV - 折扣（0~30%）；未支付/取消单实付为 0
        if status in ("paid", "refunded"):
            pay = (gmv * Decimal(str(1 - rng.uniform(0, 0.3)))).quantize(Decimal("0.01"))
            # 埋点C：支付时间 = 下单后 5 分钟 ~ 3 天
            pay_time = created + timedelta(minutes=rng.randint(5, 4320))
        else:
            pay = Decimal("0.00")
            pay_time = None  # 埋点C：未支付/取消单 pay_time 为 NULL

        orders.append((uid, channel, gmv, pay, status, created, pay_time))
        items.extend(order_items)
    return orders, items


def gen_traffic(rng: random.Random, n: int, n_users: int) -> list[tuple]:
    rows = []
    for _ in range(n):
        uid = min(max(int(rng.paretovariate(1.2)), 1), n_users)
        rows.append((uid if rng.random() > 0.02 else None,
                     weighted_choice(rng, list(zip(TRAFFIC_EVENTS, TRAFFIC_W, strict=True))),
                     sample_created_at(rng)))
    return rows


async def load_data(scale: float, dsn: str) -> None:
    rng = random.Random(SEED)
    n_users = int(SCALES["users"][0] * scale)
    n_products = int(SCALES["products"][0] * scale)
    n_orders = int(SCALES["orders"][0] * scale)
    n_traffic = int(SCALES["traffic"][0] * scale)
    print(f"[gen] 规模: users={n_users:,} products={n_products:,} orders={n_orders:,} traffic={n_traffic:,}")

    conn = await asyncpg.connect(dsn)
    try:
        # 清空（保留 schema/角色/索引）
        await conn.execute("TRUNCATE biz.traffic_logs, biz.order_items, biz.orders, biz.products, biz.users RESTART IDENTITY CASCADE")
        print("[gen] 已清空旧数据")

        users = gen_users(rng, n_users)
        await conn.copy_records_to_table("users", records=users, schema_name="biz",
                                         columns=["name", "city", "channel_source", "created_at"])
        print(f"[gen] users 写入 {len(users):,} 行")

        products = gen_products(rng, n_products)
        # order_items 需要 product id：products 的 id 从 1 连续自增，直接用序号
        prod_with_id = [(i + 1, *p) for i, p in enumerate(products)]
        await conn.copy_records_to_table("products", records=products, schema_name="biz",
                                         columns=["name", "category", "status", "list_price"])
        print(f"[gen] products 写入 {len(products):,} 行")

        # 订单+明细（分批 COPY；订单 ID 与自增序列对齐，明细引用才正确）
        total_items = 0
        batch = SCALES["orders"][1]
        for start in range(0, n_orders, batch):
            end = min(start + batch, n_orders)
            sub_rng = random.Random(SEED + start)  # 每批独立种子，内存友好
            orders, items = gen_orders_and_items(sub_rng, start + 1, end - start, n_users, prod_with_id)
            await conn.copy_records_to_table("orders", records=orders, schema_name="biz",
                                             columns=["user_id", "channel", "gmv_amount", "pay_amount",
                                                      "order_status", "created_at", "pay_time"])
            await conn.copy_records_to_table("order_items", records=items, schema_name="biz",
                                             columns=["order_id", "product_id", "quantity", "price"])
            total_items += len(items)
            print(f"[gen] orders {start + 1:,}~{end:,} / items 累计 {total_items:,}")
            del orders, items

        # COPY 不推进自增序列，显式同步，防止后续 INSERT 主键冲突
        await conn.execute("SELECT setval('biz.users_id_seq', (SELECT MAX(id) FROM biz.users))")
        await conn.execute("SELECT setval('biz.products_id_seq', (SELECT MAX(id) FROM biz.products))")
        await conn.execute("SELECT setval('biz.orders_id_seq', (SELECT MAX(id) FROM biz.orders))")
        await conn.execute("SELECT setval('biz.order_items_id_seq', (SELECT MAX(id) FROM biz.order_items))")
        await conn.execute("SELECT setval('biz.traffic_logs_id_seq', (SELECT MAX(id) FROM biz.traffic_logs))")

        traffic = gen_traffic(rng, n_traffic, n_users)
        await conn.copy_records_to_table("traffic_logs", records=traffic, schema_name="biz",
                                         columns=["user_id", "event_type", "ts"])
        print(f"[gen] traffic_logs 写入 {len(traffic):,} 行")

        # 指标口径注册表（embedding 留空，D2 灌向量）
        # ------------------------------------------------------------------
        # 【单位约定】比率类一律"百分比 + 两位小数"：ROUND(100.0 * 分子 / 分母, 2)
        # 【形态约定】usage_hint 的首段是**形态词汇**（受控词表，有测试守住）：
        #   整体类 = 单一标量；分组类 = 按维度给每组聚合值；占比类 = 本组/全表合计×100；
        #   排名类 = 排序取 TopN；人均类 = 先聚合到用户/订单再取平均；转化类 = 人数或订单数的比率/交集
        # L4 生成层按形态选 SQL 形态（见 prompts 的"形态 → SQL 写法"约定）。
        # 【为什么这么细】真实模型实测：失败几乎全部来自"术语 → 算式"的映射缺失。其中有些
        #   约定是**反直觉**的（"下单金额"是实付不是 GMV；"人均订单金额"是订单级 AVG 而不是
        #   每用户汇总再平均）——模型推不出来，只能显式登记。评测集服从本表（ADR D11）。
        # ------------------------------------------------------------------
        metrics = METRIC_DEFINITIONS
        await conn.execute("TRUNCATE biz.metric_definitions RESTART IDENTITY")
        await conn.executemany(
            "INSERT INTO biz.metric_definitions(metric_name, definition, sql_hint, usage_hint) VALUES ($1,$2,$3,$4)",
            metrics)
        print(f"[gen] metric_definitions 写入 {len(metrics)} 条（embedding 待 D2 灌入）")
    finally:
        await conn.close()


async def verify(dsn: str) -> bool:
    """数据质量检查：每一项都打印，任一硬性条件失败返回 False。"""
    conn = await asyncpg.connect(dsn)
    ok = True
    try:
        async def q(sql: str) -> tuple:
            return await conn.fetchrow(sql)

        print("\n[verify] ===== 数据质量检查 =====")
        counts = await q("SELECT (SELECT COUNT(*) FROM biz.users) u, (SELECT COUNT(*) FROM biz.products) p,"
                         " (SELECT COUNT(*) FROM biz.orders) o, (SELECT COUNT(*) FROM biz.order_items) i,"
                         " (SELECT COUNT(*) FROM biz.traffic_logs) t")
        print(f"[verify] 行数: users={counts['u']:,} products={counts['p']:,} orders={counts['o']:,} "
              f"order_items={counts['i']:,} traffic={counts['t']:,}")

        # 1. 外键完整性（孤儿行必须为 0）
        orphan_u = (await q("SELECT COUNT(*) c FROM biz.orders o LEFT JOIN biz.users u ON o.user_id=u.id WHERE u.id IS NULL"))["c"]
        orphan_i = (await q("SELECT COUNT(*) c FROM biz.order_items i LEFT JOIN biz.orders o ON i.order_id=o.id WHERE o.id IS NULL"))["c"]
        orphan_p = (await q("SELECT COUNT(*) c FROM biz.order_items i LEFT JOIN biz.products p ON i.product_id=p.id WHERE p.id IS NULL"))["c"]
        print(f"[verify] 外键孤儿行: orders→users={orphan_u} items→orders={orphan_i} items→products={orphan_p}")
        if orphan_u or orphan_i or orphan_p:
            ok = False
            print("[verify] ❌ 外键完整性失败")

        # 2. 埋点B：products.status 脏值存在性
        st = await q("SELECT COUNT(*) FILTER (WHERE status IS NULL) null_cnt, COUNT(*) FILTER (WHERE status=2) presale FROM biz.products")
        print(f"[verify] 埋点B status: NULL={st['null_cnt']} 预售={st['presale']}（应 >0）")
        if st["null_cnt"] == 0 and st["presale"] == 0:
            print("[verify] ⚠️ 埋点B 未生效")

        # 3. 埋点C：未支付/取消单不得有 pay_time；已支付单必须有
        bad1 = (await q("SELECT COUNT(*) c FROM biz.orders WHERE order_status IN ('created','cancelled') AND pay_time IS NOT NULL"))["c"]
        bad2 = (await q("SELECT COUNT(*) c FROM biz.orders WHERE order_status IN ('paid','refunded') AND (pay_time IS NULL OR pay_amount = 0)"))["c"]
        print(f"[verify] 埋点C: 未支付却有pay_time={bad1} 已支付却缺pay_time/金额={bad2}（应均为 0）")
        if bad1 or bad2:
            ok = False
            print("[verify] ❌ 埋点C 一致性失败")

        # 4. 金额一致性：gmv_amount == SUM(price*quantity)
        bad_amt = (await q("SELECT COUNT(*) c FROM (SELECT o.id, o.gmv_amount, COALESCE(SUM(i.price*i.quantity),0) s"
                           " FROM biz.orders o LEFT JOIN biz.order_items i ON i.order_id=o.id"
                           " GROUP BY o.id, o.gmv_amount) t WHERE ABS(t.gmv_amount - t.s) > 0.01"))["c"]
        print(f"[verify] 金额不一致订单数: {bad_amt}（应为 0）")
        if bad_amt:
            ok = False
            print("[verify] ❌ 金额一致性失败")

        # 5. 帕累托分布：头部用户订单占比
        pareto = await q("SELECT SUM(cnt) FILTER (WHERE rnk <= 100) top100, SUM(cnt) total, MAX(cnt) max_cnt FROM"
                         " (SELECT user_id, COUNT(*) cnt, ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) rnk"
                         "  FROM biz.orders GROUP BY user_id) t")
        share = float(pareto["top100"] or 0) / float(pareto["total"] or 1)
        print(f"[verify] 帕累托: Top100 用户贡献 {share:.1%} 订单，单人最多 {pareto['max_cnt']:,} 单")

        # 6. 大促尖峰：双11 当日订单量 vs 平日
        spike = await q("SELECT COUNT(*) FILTER (WHERE to_char(created_at,'MM-DD')='11-11') d11,"
                        " COUNT(*) FILTER (WHERE to_char(created_at,'MM-DD')='07-15') normal FROM biz.orders")
        print(f"[verify] 尖峰: 双11={spike['d11']:,} 单 vs 平日={spike['normal']:,} 单")

        print(f"[verify] ===== 检查{'通过 ✅' if ok else '失败 ❌'} =====")
        return ok
    finally:
        await conn.close()


async def _invalidate_caches() -> None:
    """失效 schema 注册表与指标口径的 Redis 缓存（两级都清）。

    脚本直连 Redis 而不是 import app 模块：保持脚本独立可移植
    （CI/本地不依赖 venv 里的 app 包也能跑）。
    """
    client = aioredis.from_url(_redis_url(), encoding="utf-8")
    try:
        await client.delete("schema:allowed_tables", "schema:metric_definitions")
    finally:
        await client.aclose()


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")

def main() -> int:
    ap = argparse.ArgumentParser(description="生成 mock 电商数据")
    ap.add_argument("--scale", type=float, default=1.0, help="规模倍率，0.1 = 十分之一")
    ap.add_argument("--verify", action="store_true", help="只验证不生成")
    ap.add_argument("--dsn", default=ADMIN_DSN, help="管理员连接串")
    args = ap.parse_args()

    if args.verify:
        return 0 if asyncio.run(verify(args.dsn)) else 1
    asyncio.run(load_data(args.scale, args.dsn))
    # 数据重灌后失效 schema/口径两级 Redis 缓存：metric_definitions 是被重写的，
    # 不清缓存的话 Agent 最长 1 小时还在用旧口径（最终审查实测发现的真 bug）。
    try:
        asyncio.run(_invalidate_caches())
    except Exception as e:  # Redis 不在线不阻塞生成（本地脚本场景）
        print(f"[warn] 缓存失效跳过: {e}")
    return 0 if asyncio.run(verify(args.dsn)) else 1


if __name__ == "__main__":
    sys.exit(main())
