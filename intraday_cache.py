"""每日拉取 ETF 1min bar 并累加缓存。

新浪接口固定返回最新 1970 根 1min bar (~9 交易日), 每日拉取按 day 去重合并,
缓存逐日增长, 突破 1970 根历史深度限制。

用法: python intraday_cache.py [代码]     # 默认 159696
输出: cache/intraday_{code}_1min.parquet  列: day open high low close volume amount
      etf_data.js  (window.ETF_DATA, 供 etf.html 渲染日K + 分时)
"""
import json
import sys
from pathlib import Path

import pandas as pd


def export_js(df: pd.DataFrame, code: str, out: str = "etf_data.js") -> int:
    """按日聚合成日K + 每日分时序列, 写 etf_data.js。返回导出天数。"""
    df = df.assign(date=df["day"].dt.strftime("%Y-%m-%d"),
                   tod=df["day"].dt.strftime("%H:%M"))
    # ponytail: 首拉边界日只有半天 bar, OHLC 失真, 不足 200 根整天剔除
    df = df.groupby("date").filter(lambda g: len(g) >= 200)
    g = df.groupby("date")
    kline = [[d, round(r["o"], 3), round(r["c"], 3), round(r["l"], 3), round(r["h"], 3),
              int(r["v"]), int(r["a"])]
             for d, r in pd.DataFrame({
                 "o": g["open"].first(), "c": g["close"].last(),
                 "l": g["low"].min(), "h": g["high"].max(),
                 "v": g["volume"].sum(), "a": g["amount"].sum()}).iterrows()]
    intraday = {d: {"t": sub["tod"].tolist(),
                    "c": [round(x, 3) for x in sub["close"]],
                    "v": [int(x) for x in sub["volume"]]}
                for d, sub in g}
    data = {"code": code,
            "updated": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M"),
            "kline": kline, "intraday": intraday}
    Path(out).write_text("window.ETF_DATA = " + json.dumps(data, separators=(",", ":"),
                                                           ensure_ascii=False) + ";\n")
    return len(kline)


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "159696"
    path = Path(f"cache/intraday_{code}_1min.parquet")

    from median_trend import is_trading_day
    today = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
    if not is_trading_day(today):
        print(f"{today} 非交易日, 跳过")
        return

    import akshare as ak
    mkt = "sh" if code[0] in "56" else "sz"
    new = ak.stock_zh_a_minute(symbol=f"{mkt}{code}", period="1", adjust="")
    new["day"] = pd.to_datetime(new["day"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        new[c] = new[c].astype(float)

    if path.exists():
        old = pd.read_parquet(path)
        merged = pd.concat([old, new]).drop_duplicates("day", keep="last").sort_values("day")
    else:
        merged = new.sort_values("day")

    merged.to_parquet(path, index=False)
    days = export_js(merged, code)
    print(f"{code} 1min: 拉取{len(new)}根 合并后{len(merged)}根 导出{days}天 "
          f"{merged['day'].min()} ~ {merged['day'].max()}")


if __name__ == "__main__":
    main()
