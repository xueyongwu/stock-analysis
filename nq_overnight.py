"""纳指期货(NQ主连)隔夜涨跌: 前一A股交易日15:00 -> 当日9:30 (北京时间)。

新浪全球期货分时接口只返回当前一个盘(~1380根1min, 北京6:00~次日5:00),
每日拉取按时间戳去重累积进 cache/nq_min.parquet, 攒出跨日窗口。
冷启动首日缺前一日15:00基准, 次个交易日起出数。CME休市日(美国假日)自动跳过。

A股交易日历直接取 cache/daily_pctchg.parquet 的日期列, 不额外调接口。

用法: python nq_overnight.py
输出: cache/nq_min.parquet (dt, price)
      nq_data.js (window.NQ_OVERNIGHT, index.html 隔夜卡片)
"""
import json
import re
from pathlib import Path

import pandas as pd
import requests

URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/"
       "GlobalFuturesService.getGlobalFuturesMinLine?symbol=NQ")
CACHE = Path("cache/nq_min.parquet")


def fetch_bars() -> pd.DataFrame:
    r = requests.get(URL, headers={"Referer": "https://finance.sina.com.cn"}, timeout=20)
    r.raise_for_status()
    rows = json.loads(re.search(r"\((.*)\)", r.text, re.S).group(1))["minLine_1d"]
    # 首行比常规行多4个前缀字段(日期/昨结/交易所/空), 统一从尾部取: [-1]=时间戳 [-5]=价
    return pd.DataFrame({"dt": pd.to_datetime([x[-1] for x in rows]),
                         "price": [float(x[-5]) for x in rows]})


def trading_days() -> list[pd.Timestamp]:
    d = pd.read_parquet("cache/daily_pctchg.parquet", columns=["date"])["date"].unique()
    return sorted(pd.to_datetime(d))


def overnight(bars: pd.DataFrame, tdays: list[pd.Timestamp]) -> list[dict]:
    s = pd.Series(bars["price"].values, index=bars["dt"]).sort_index()
    items = []
    for prev, d in zip(tdays, tdays[1:]):
        base = s.asof(prev + pd.Timedelta(hours=15))  # 前一交易日15:00最近价
        opens = s[(s.index >= d + pd.Timedelta(hours=6)) &
                  (s.index <= d + pd.Timedelta(hours=9, minutes=30))]
        if pd.isna(base) or opens.empty:  # 缓存未覆盖 或 当日晨CME无盘
            continue
        o = float(opens.iloc[-1])
        items.append({"d": d.strftime("%Y-%m-%d"),
                      "pct": round((o / base - 1) * 100, 2) + 0,  # +0 归一化 -0.0
                      "base": round(float(base), 2), "open": round(o, 2)})

    # 半程点: 最后一个交易日15:00 -> 最新bar, 下一交易日9:30后被完整点替代
    base = s.asof(tdays[-1] + pd.Timedelta(hours=15))
    tail = s[s.index > tdays[-1] + pd.Timedelta(hours=15)]
    if not pd.isna(base) and not tail.empty:
        o = float(tail.iloc[-1])
        items.append({"d": tail.index[-1].strftime("%Y-%m-%d"),
                      "t": tail.index[-1].strftime("%m-%d %H:%M"),
                      "pct": round((o / base - 1) * 100, 2) + 0,  # +0 归一化 -0.0
                      "base": round(float(base), 2), "open": round(o, 2),
                      "partial": True})
    return items


def main():
    new = fetch_bars()
    if CACHE.exists():
        merged = (pd.concat([pd.read_parquet(CACHE), new])
                  .drop_duplicates("dt", keep="last").sort_values("dt"))
    else:
        merged = new
    merged.to_parquet(CACHE, index=False)

    now = pd.Timestamp.now(tz="Asia/Shanghai")
    items = [x for x in overnight(merged, trading_days())
             if x["d"] >= f"{now.year}-01-01"]
    payload = {"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items}
    Path("nq_data.js").write_text(
        "window.NQ_OVERNIGHT=" + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8")
    print(f"NQ 1min: 拉取{len(new)}根 合并后{len(merged)}根 "
          f"({merged['dt'].min()} ~ {merged['dt'].max()}) 隔夜点位 {len(items)} 天")


if __name__ == "__main__":
    main()
