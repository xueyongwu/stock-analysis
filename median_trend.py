"""A股全市场每日涨跌中位数趋势图。

免费数据源: baostock(历史日线, 无需 token) + 腾讯 qt.gtimg.cn(当日批量快照)。
思路: 逐股拉今年以来日线涨跌幅 -> 缓存 parquet(断点续传) -> 按日取中位数
      -> 导出 data.js, index.html 用 ECharts 画(双栏: 日中位数 + 累计)。
首次全量 ~5000 股约 10-20 分钟。之后走缓存, 秒级; 收盘后 --update 增量。
注: --update 默认走腾讯批量(15:00 收盘即可用, ~1.5 秒);
    缓存有缺口或腾讯不可用时回退 baostock(逐股慢, 且当日数据 ~17:30 后才有)。

用法:
    python median_trend.py                 # 无缓存则全量拉, 有则直接导出 data.js
    python median_trend.py --refresh       # 强制重拉
    python median_trend.py --update        # 收盘后增量重拉最近10天
    然后浏览器打开 index.html

依赖: pip install baostock pandas pyarrow
"""
import argparse
import json
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)
RAW = CACHE / "daily_pctchg.parquet"  # 长表: date, code, pct
ST = CACHE / "st_codes.json"          # ST 名单快照(见 st_codes)
ST_TTL_DAYS = 7

_bs_depth = 0


@contextmanager
def bs_session():
    """baostock 会话(全局单例, 可嵌套)。

    login 握手是秒级开销, 原先每个查询各登一次; 嵌套时只有最外层真正登录/登出。
    """
    global _bs_depth
    import baostock as bs
    if _bs_depth == 0:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    _bs_depth += 1
    try:
        yield bs
    finally:
        _bs_depth -= 1
        if _bs_depth == 0:
            try:
                bs.logout()
            except Exception:
                pass

# A 股宇宙前缀(baostock 格式): 沪主板/科创 sh.6, 深主板/中小 sz.0, 创业板 sz.30(300/301/302)。
# 用 sz.30 而非 sz.3, 否则 sz.399* 深证指数会混入。北交所不含。
A_PREFIXES = ("sh.6", "sz.0", "sz.30")


def all_a_codes(day: str) -> list[str]:
    """指定交易日全部 A 股代码(baostock 格式 sh.600000 / sz.000001)。

    query_all_stock 含指数(sh.000*/sz.399*)和 B 股(sh.900*/sz.200*), 过滤只留 A 股:
    见 A_PREFIXES。北交所 baostock 不含。
    """
    with bs_session() as bs:
        d = pd.Timestamp(day)
        codes = []
        for _ in range(7):  # end 非交易日则回退找最近交易日
            rs = bs.query_all_stock(day=d.strftime("%Y-%m-%d"))
            while rs.error_code == "0" and rs.next():
                codes.append(rs.get_row_data()[0])
            if codes:
                break
            d -= pd.Timedelta(days=1)
    return [c for c in codes if c.startswith(A_PREFIXES)]


def fetch_history(codes: list[str], start: str, end: str, skip_done: bool = True) -> pd.DataFrame:
    """baostock 逐股拉日线涨跌幅, 每100股落盘。

    skip_done=True: 首次全量, 跳过已缓存 code(断点续传)。
    skip_done=False: 增量更新, 全部重拉 start..end 窗口, 按 (date,code) 去重合并。
    """
    import baostock as bs

    have = pd.read_parquet(RAW) if RAW.exists() else pd.DataFrame(columns=["date", "code", "pct"])
    done = set(have["code"].unique()) if skip_done else set()
    todo = [c for c in codes if c not in done]
    rows = [have]

    def _login():
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")

    def _fetch_one(code):
        rs = bs.query_history_k_data_plus(
            code, "date,pctChg", start_date=start, end_date=end,
            frequency="d", adjustflag="3",  # 3=不复权, 涨跌幅用原始
        )
        if rs.error_code != "0":
            raise RuntimeError(rs.error_msg)  # 触发重连重试
        recs = []
        while rs.next():
            recs.append(rs.get_row_data())
        return recs

    print(f"待拉取 {len(todo)} 股(已缓存 {len(done)})。", flush=True)
    with bs_session():
        for i, code in enumerate(todo, 1):
            recs = None
            for attempt in range(3):  # 单股失败重连重试, 不阻断整轮
                try:
                    recs = _fetch_one(code)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  skip {code}: {e}", flush=True)
                    else:
                        try:
                            bs.logout()
                        except Exception:
                            pass
                        _login()  # 断连后重登
            if recs:
                d = pd.DataFrame(recs, columns=["date", "pct"])
                d = d[d["pct"] != ""]  # 停牌日 pctChg 为空 -> 剔除
                if not d.empty:
                    rows.append(pd.DataFrame({
                        "date": pd.to_datetime(d["date"]),
                        "code": code,
                        "pct": d["pct"].astype(float),
                    }))
            if i % 100 == 0:
                print(f"  {i}/{len(todo)} ...", flush=True)
                pd.concat(rows, ignore_index=True).to_parquet(RAW, index=False)  # 阶段落盘

    out = pd.concat(rows, ignore_index=True).drop_duplicates(["date", "code"])
    out.to_parquet(RAW, index=False)  # 长表不存行号, 省 ~5MB
    return out


TENCENT_Q = "https://qt.gtimg.cn/q="
TENCENT_CHUNK = 800  # 实测每请求上限 900 只(1000 返回空), 取 800 留余量


def parse_tencent_quotes(text: str, stamp: str, sym2code: dict[str, str]) -> dict[str, float]:
    """解析 qt.gtimg.cn 返回体 -> {baostock代码: 当日涨跌幅%}。

    行格式 v_sh600000="1~名称~600000~现价~昨收~今开~成交量(手)~...";
    字段: [6]成交量 [30]时间戳 yyyymmddHHMMSS [32]涨跌幅%。
    """
    out = {}
    for line in text.split(";"):
        k, _, v = line.partition("=")
        code = sym2code.get(k.strip()[2:])  # 去掉 v_ 前缀
        if not code:
            continue
        f = v.strip().strip('"').split("~")
        if len(f) < 33 or not f[30].startswith(stamp):
            continue  # 字段异常, 或快照不是当日(隔日跑/长期停牌的陈旧价)
        if float(f[6] or 0) <= 0:
            continue  # 成交量 0 = 停牌, 腾讯给 pct=0.00 而非空, 不剔会拉偏中位数
        out[code] = float(f[32] or 0)
    return out


def update_today_tencent(codes: list[str], day: str) -> pd.DataFrame | None:
    """腾讯批量快照拉当日涨跌幅并并入缓存: 全市场 ~7 个请求 1.5 秒。

    与 baostock 逐股窗口重拉(5000+ 请求)结果实测一致(qfq 口径校验 30 股 0 偏差),
    且 15:00 收盘即可用, 不必等 baostock 的 ~17:30。
    覆盖率不足或请求失败返回 None, 由调用方回退 baostock。
    """
    import requests

    stamp = day.replace("-", "")
    quotes = {}
    try:
        for i in range(0, len(codes), TENCENT_CHUNK):
            chunk = codes[i:i + TENCENT_CHUNK]
            sym2code = {c.replace(".", ""): c for c in chunk}
            r = requests.get(TENCENT_Q + ",".join(sym2code),
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.raise_for_status()
            quotes.update(parse_tencent_quotes(r.content.decode("gbk", "ignore"), stamp, sym2code))
    except Exception as e:
        print(f"腾讯快照失败({e}), 回退 baostock。", flush=True)
        return None

    if len(quotes) < len(codes) * 0.9:  # 停牌一般远不到 10%, 缺这么多说明源有问题
        print(f"腾讯快照仅 {len(quotes)}/{len(codes)} 只, 回退 baostock。", flush=True)
        return None

    have = pd.read_parquet(RAW) if RAW.exists() else pd.DataFrame(columns=["date", "code", "pct"])
    today = pd.DataFrame({"date": pd.Timestamp(day), "code": list(quotes), "pct": list(quotes.values())})
    out = (pd.concat([have, today], ignore_index=True)
             .drop_duplicates(["date", "code"], keep="last"))  # 重跑以新快照为准
    out.to_parquet(RAW, index=False)  # 长表不存行号, 省 ~5MB
    print(f"腾讯快照 {day}: {len(quotes)} 只。", flush=True)
    return out


def trading_days(start: str, end: str) -> list[str]:
    """baostock 交易日历。用于节假日空跑保护 + 检测缓存缺口。"""
    with bs_session() as bs:
        rs = bs.query_trade_dates(start_date=start, end_date=end)
        days = []
        while rs.error_code == "0" and rs.next():
            d, is_open = rs.get_row_data()[:2]
            if is_open == "1":
                days.append(d)
        return days


def is_trading_day(day: str) -> bool:
    """节假日 cron 空跑, 不写脏数据。"""
    return day in trading_days(day, day)


def st_codes() -> set[str]:
    """名称含 ST 的代码集合(其 ±5% 涨跌停与普通涨跌无法区分, 统计时剔除)。

    query_stock_basic 要拉全市场 5000+ 行, 是本脚本最慢的单次调用; 戴帽摘帽是低频事件,
    结果缓存 ST_TTL_DAYS 天。缓存文件入库, 免得每次 CI 都重拉。
    """
    if ST.exists():
        blob = json.loads(ST.read_text(encoding="utf-8"))
        if pd.Timestamp(blob["updated"]) > pd.Timestamp.now() - pd.Timedelta(days=ST_TTL_DAYS):
            return set(blob["codes"])
    with bs_session() as bs:
        rs = bs.query_stock_basic()
        out = set()
        while rs.error_code == "0" and rs.next():
            code, name = rs.get_row_data()[:2]
            if "ST" in name.upper():
                out.add(code)
    if not out:
        raise RuntimeError("ST 名单为空, 疑似接口异常")  # 不覆盖旧缓存
    ST.write_text(json.dumps({"updated": pd.Timestamp.now().strftime("%Y-%m-%d"),
                              "codes": sorted(out)}), encoding="utf-8")
    return out


def update_incremental(end: str) -> pd.DataFrame | None:
    """收盘后增量。返回 None = 非交易日或拿不到可信当日数据, 调用方直接退出不写盘。

    baostock 只用来查交易日历、代码表和回退拉取——数据主源已是腾讯, 所以它挂了要能降级:
    代码表退回缓存里最近交易日那份(漏掉当天新上市的几只, 5000 只样本的中位数无感),
    交易日判断交给腾讯快照自己——节假日快照返回的是上个交易日的陈旧时间戳,
    parse_tencent_quotes 的日期校验会全过滤掉, 覆盖率为 0 自然判定不写。
    """
    recent = (pd.Timestamp(end) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    days, codes, bs_ok = None, None, True
    try:
        with bs_session():  # 日历/代码表共用一次登录
            days = trading_days(recent, end)
            if end not in days:
                print(f"{end} 非交易日, 跳过。", flush=True)
                return None
            codes = all_a_codes(end)
    except Exception as e:
        bs_ok = False
        print(f"baostock 不可用({e}), 降级: 缓存代码表 + 腾讯快照自判交易日。", flush=True)

    have = pd.read_parquet(RAW) if RAW.exists() else None
    if codes is None:
        if have is None:
            raise RuntimeError("baostock 不可用且无缓存, 无从确定股票池")
        last = have["date"].max()
        codes = sorted(have.loc[have["date"] == last, "code"].unique())
        print(f"用 {last:%Y-%m-%d} 的缓存代码表 {len(codes)} 只。", flush=True)

    cached = set(have["date"].dt.strftime("%Y-%m-%d")) if have is not None else set()
    gaps = [d for d in (days or []) if d != end and d not in cached]
    df = None
    if gaps:  # CI 漏跑过, 腾讯只给当日, 补不了 -> 走 baostock 窗口
        print(f"缓存缺 {gaps}, 走 baostock 重拉窗口。", flush=True)
    else:
        df = update_today_tencent(codes, end)
    if df is None and not bs_ok:  # 回退路径也要 baostock, 它挂着就别写半截数据
        print("腾讯无当日数据且 baostock 不可用, 本次不更新。", flush=True)
        return None
    if df is None:  # baostock 增量: 重拉最近10天窗口, 按 (date,code) 去重合并
        df = fetch_history(codes, recent, end, skip_done=False)
    return df


def sanity_check(p: dict):
    """写盘前体检。字段错位/源返回半截数据时直接抛, 不留脏 data.js。

    只查内容不查新鲜度: 节假日本就不该有新数据, 没写就没 commit, 不算故障。
    """
    n = len(p["dates"])
    assert n and all(len(p[k]) == n for k in ("median", "cum", "count", "upRatio")), "列长度不齐"
    assert len(set(p["dates"])) == n, "日期重复"
    assert p["dates"] == sorted(p["dates"]), "日期未升序"
    today = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
    assert p["dates"][-1] <= today, f"出现未来日期 {p['dates'][-1]}"
    assert all(abs(x) < 15 for x in p["median"]), "中位数越界(疑似字段错位)"
    assert all(0 <= x <= 100 for x in p["upRatio"]), "上涨占比越界"
    assert p["count"][-1] > 4000, f"最新交易日仅 {p['count'][-1]} 只, 疑似拉取不全"


def export_data_js(df: pd.DataFrame, out: Path):
    """按日聚合 -> 写 data.js 供 index.html (ECharts) 读取。"""
    g = df.groupby("date")["pct"]
    med = g.median().sort_index().round(3)
    n = g.size().reindex(med.index)              # 每日样本量
    up = df[df["pct"] > 0].groupby("date").size().reindex(med.index).fillna(0)
    cum = med.cumsum().round(3)                   # 累计中位数

    ld = df[df["date"] == med.index[-1]]  # 最新交易日截面
    # 涨跌停按板块阈值近似(科创68/创业30 ±19.9%, 其余 ±9.9%), ST 剔除; 拉名单失败则降级不剔
    try:
        lu = ld[~ld["code"].isin(st_codes())]
    except Exception as e:
        print(f"ST 名单拉取失败, 涨跌停统计未剔 ST: {e}", flush=True)
        lu = ld
    lim = lu["code"].str.startswith(("sh.68", "sz.30")).map({True: 19.9, False: 9.9})

    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in med.index],
        "median": med.tolist(),
        "cum": cum.tolist(),
        "count": n.astype(int).tolist(),
        "upRatio": (up / n * 100).round(1).tolist(),  # 上涨家数占比 %
        "latest": {
            "limitUp": int((lu["pct"] >= lim).sum()),
            "limitDown": int((lu["pct"] <= -lim).sum()),
            "up": int((ld["pct"] > 0).sum()),
            "flat": int((ld["pct"] == 0).sum()),
            "down": int((ld["pct"] < 0).sum()),
        },
        "updated": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M"),
    }
    sanity_check(payload)  # 脏数据宁可让 CI 红, 也别 commit 进去(次日 --update 不回补)
    out.write_text("window.MEDIAN_DATA=" + json.dumps(payload, ensure_ascii=False) + ";\n",
                   encoding="utf-8")
    print(f"数据已导出: {out}  ({len(med)} 交易日, 中位数样本 ~{int(n.median())} 股/日)")
    print(med.tail(10).to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=pd.Timestamp.now().strftime("%Y-01-01"))
    ap.add_argument("--end", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--update", action="store_true", help="收盘后追加今日快照")
    a = ap.parse_args()

    if a.update:
        # 盘中快照会被当成收盘价写死(次日的 --update 只取新一天, 不回补), 故未收盘不落库
        now = pd.Timestamp.now(tz="Asia/Shanghai")
        if a.end == now.strftime("%Y-%m-%d") and now.hour < 15 and RAW.exists():
            print(f"{a.end} 尚未收盘({now:%H:%M}), 不落库, 仅用缓存导出。", flush=True)
            df = pd.read_parquet(RAW)
        else:
            df = update_incremental(a.end)
            if df is None:  # 非交易日, 或没有可信的当日数据
                return
    elif a.refresh or not RAW.exists():
        if a.refresh and RAW.exists():
            RAW.unlink()
        print("全市场拉取中(首次 ~10-20 分钟, 断点续传落盘)...")
        df = fetch_history(all_a_codes(a.end), a.start, a.end)
    else:
        print("用缓存。--refresh 重拉, --update 追加今日。")
        df = pd.read_parquet(RAW)

    df = df[df["date"] >= pd.Timestamp.now().strftime("%Y-01-01")]  # 只导出今年以来
    export_data_js(df, CACHE.parent / "data.js")


if __name__ == "__main__":
    main()
