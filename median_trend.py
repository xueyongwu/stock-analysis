"""A股全市场每日涨跌中位数趋势图。

免费数据源: baostock(日线, 无需 token)。
思路: 逐股拉今年以来日线涨跌幅 -> 缓存 parquet(断点续传) -> 按日取中位数
      -> 导出 data.js, index.html 用 ECharts 画(双栏: 日中位数 + 累计)。
首次全量 ~5000 股约 10-20 分钟。之后走缓存, 秒级; 收盘后 --update 增量。
注: baostock 当日数据一般傍晚(~17:30后)才可用。

用法:
    python median_trend.py                 # 无缓存则全量拉, 有则直接导出 data.js
    python median_trend.py --refresh       # 强制重拉
    python median_trend.py --update        # 收盘后增量重拉最近10天
    然后浏览器打开 index.html

依赖: pip install baostock pandas pyarrow
"""
import argparse
import json
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)
RAW = CACHE / "daily_pctchg.parquet"  # 长表: date, code, pct

# A 股宇宙前缀(baostock 格式): 沪主板/科创 sh.6, 深主板/中小 sz.0, 创业板 sz.30(300/301/302)。
# 用 sz.30 而非 sz.3, 否则 sz.399* 深证指数会混入。北交所不含。
A_PREFIXES = ("sh.6", "sz.0", "sz.30")


def all_a_codes(day: str) -> list[str]:
    """指定交易日全部 A 股代码(baostock 格式 sh.600000 / sz.000001)。

    query_all_stock 含指数(sh.000*/sz.399*)和 B 股(sh.900*/sz.200*), 过滤只留 A 股:
    见 A_PREFIXES。北交所 baostock 不含。
    """
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        d = pd.Timestamp(day)
        codes = []
        for _ in range(7):  # end 非交易日则回退找最近交易日
            rs = bs.query_all_stock(day=d.strftime("%Y-%m-%d"))
            while rs.error_code == "0" and rs.next():
                codes.append(rs.get_row_data()[0])
            if codes:
                break
            d -= pd.Timedelta(days=1)
    finally:
        bs.logout()
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

    _login()
    print(f"待拉取 {len(todo)} 股(已缓存 {len(done)})。", flush=True)
    try:
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
                pd.concat(rows, ignore_index=True).to_parquet(RAW)  # 阶段落盘
    finally:
        try:
            bs.logout()
        except Exception:
            pass

    out = pd.concat(rows, ignore_index=True).drop_duplicates(["date", "code"])
    out.to_parquet(RAW)
    return out


def is_trading_day(day: str) -> bool:
    """baostock 判断是否交易日(节假日 cron 空跑, 不写脏数据)。"""
    import baostock as bs
    bs.login()
    try:
        rs = bs.query_trade_dates(start_date=day, end_date=day)
        row = rs.get_row_data() if (rs.error_code == "0" and rs.next()) else None
    finally:
        bs.logout()
    return bool(row) and row[1] == "1"


def export_data_js(df: pd.DataFrame, out: Path):
    """按日聚合 -> 写 data.js 供 index.html (ECharts) 读取。"""
    g = df.groupby("date")["pct"]
    med = g.median().sort_index().round(3)
    n = g.size().reindex(med.index)              # 每日样本量
    up = df[df["pct"] > 0].groupby("date").size().reindex(med.index).fillna(0)
    cum = med.cumsum().round(3)                   # 累计中位数

    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in med.index],
        "median": med.tolist(),
        "cum": cum.tolist(),
        "count": n.astype(int).tolist(),
        "upRatio": (up / n * 100).round(1).tolist(),  # 上涨家数占比 %
        "updated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
    }
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
        if not is_trading_day(a.end):
            print(f"{a.end} 非交易日, 跳过。", flush=True)
            return
        # baostock 增量: 重拉最近10天窗口, 按 (date,code) 去重合并
        recent = (pd.Timestamp(a.end) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        df = fetch_history(all_a_codes(a.end), recent, a.end, skip_done=False)
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
