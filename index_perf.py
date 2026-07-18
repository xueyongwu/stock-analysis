"""宽基/特色指数今年以来涨跌幅 -> idx_data.js (index.html 排名条形图)。

数据源(免费):
  sina    akshare stock_zh_index_daily        大部分沪深/北证指数
  csindex akshare stock_zh_index_hist_csindex 中证2000(新浪无 932000)
  ths     同花顺 d.10jqka.com.cn 日线接口      微盘股/可转债(883418/883981, 同花顺自编指数)

基准 = 上年最后交易日收盘, YTD = 最新收盘/基准 - 1。
单指数失败跳过不阻断, 页面少一根条。

用法: python index_perf.py
"""
import json
from pathlib import Path

import pandas as pd

INDICES = [  # (名称, 源, 代码)
    ("上证50", "sina", "sh000016"),
    ("沪深300", "sina", "sh000300"),
    ("中证A500", "sina", "sh000510"),
    ("中证500", "sina", "sh000905"),
    ("中证1000", "sina", "sh000852"),
    ("中证2000", "csindex", "932000"),
    ("微盘股", "ths", "883418"),
    ("创业板50", "sina", "sz399673"),
    ("科创50", "sina", "sh000688"),
    ("科创100", "sina", "sh000698"),
    ("科创200", "sina", "sh000699"),
    ("北证50", "sina", "bj899050"),
    ("可转债", "ths", "883981"),
]


def sina_close(symbol: str) -> pd.Series:
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol=symbol)
    return pd.Series(df["close"].values, index=pd.to_datetime(df["date"]))


def csindex_close(symbol: str, start: str, end: str) -> pd.Series:
    import akshare as ak
    df = ak.stock_zh_index_hist_csindex(symbol=symbol, start_date=start, end_date=end)
    return pd.Series(df["收盘"].values, index=pd.to_datetime(df["日期"]))


def ths_close(symbol: str, years: list[int]) -> pd.Series:
    """同花顺板块/自编指数日线。akshare 的封装按板块名查代码, 883* 指数不在名单里, 直连接口。"""
    import py_mini_racer
    import requests
    from akshare.datasets import get_ths_js

    js = py_mini_racer.MiniRacer()
    js.eval(Path(get_ths_js("ths.js")).read_text())
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "http://q.10jqka.com.cn",
        "Host": "d.10jqka.com.cn",
        "Cookie": f"v={js.call('v')}",
    }
    rows = {}
    for y in years:
        r = requests.get(f"https://d.10jqka.com.cn/v4/line/bk_{symbol}/01/{y}.js",
                         headers=headers, timeout=15)
        r.raise_for_status()
        t = r.text
        payload = json.loads(t[t.find("{"):t.rfind("}") + 1])
        for rec in payload["data"].split(";"):
            f = rec.split(",")  # date,open,high,low,close,...
            rows[f[0]] = float(f[4])
    s = pd.Series(rows)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def main():
    now = pd.Timestamp.now(tz="Asia/Shanghai")
    jan1 = pd.Timestamp(f"{now.year}-01-01")
    items = []
    for name, src, sym in INDICES:
        try:
            if src == "sina":
                s = sina_close(sym)
            elif src == "csindex":
                s = csindex_close(sym, f"{now.year - 1}1201", now.strftime("%Y%m%d"))
            else:
                s = ths_close(sym, [now.year - 1, now.year])
            base = s[s.index < jan1].iloc[-1]   # 上年最后交易日收盘
            cur = s[s.index >= jan1]
            items.append({
                "name": name,
                "ytd": round((cur.iloc[-1] / base - 1) * 100, 2),
                "close": round(float(cur.iloc[-1]), 2),
                "date": cur.index[-1].strftime("%Y-%m-%d"),
            })
            print(f"{name:<6} {items[-1]['ytd']:+7.2f}%  ({items[-1]['date']})", flush=True)
        except Exception as e:
            print(f"skip {name}({src} {sym}): {e}", flush=True)

    if not items:
        raise SystemExit("全部指数拉取失败, 不写 idx_data.js")
    items.sort(key=lambda x: x["ytd"], reverse=True)
    payload = {"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items}
    out = Path(__file__).parent / "idx_data.js"
    out.write_text("window.INDEX_YTD=" + json.dumps(payload, ensure_ascii=False) + ";\n",
                   encoding="utf-8")
    print(f"已导出: {out} ({len(items)} 个指数)")


if __name__ == "__main__":
    main()
