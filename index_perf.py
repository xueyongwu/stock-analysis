"""宽基/特色指数今年以来涨跌幅 -> idx_data.js (index.html 排名条形图)。

数据源(免费):
  tx      腾讯 web.ifzq.gtimg.cn 日线接口      沪深/北证指数, 一次请求给全年日线
  sina    akshare stock_zh_index_daily        tx 的备源(akshare 封装随网站变, 故降为备)
  csindex akshare stock_zh_index_hist_csindex 中证2000(新浪/腾讯都无 932000)
  ths     同花顺 d.10jqka.com.cn 日线接口      微盘股/可转债(883418/883981, 同花顺自编指数)

基准 = 上年最后交易日收盘, YTD = 最新收盘/基准 - 1。
单指数全部源失败时退回上次 idx_data.js 里的值并标 stale, 页面灰显; 没有旧值才少一根条。

用法: python index_perf.py
"""
import json
from pathlib import Path

import pandas as pd

TX = ("tx", "sina")  # 腾讯为主, 新浪兜底

INDICES = [  # (名称, 源(按序尝试), 代码)
    ("上证50", TX, "sh000016"),
    ("沪深300", TX, "sh000300"),
    ("中证A500", TX, "sh000510"),
    ("中证500", TX, "sh000905"),
    ("中证1000", TX, "sh000852"),
    ("中证2000", ("csindex",), "932000"),
    ("微盘股", ("ths",), "883418"),
    ("创业板50", TX, "sz399673"),
    ("科创50", TX, "sh000688"),
    ("科创100", TX, "sh000698"),
    ("科创200", TX, "sh000699"),
    ("北证50", TX, "bj899050"),
    ("可转债", ("ths",), "883981"),
]


def tencent_close(symbol: str) -> pd.Series:
    """腾讯日线收盘序列。400 根足够覆盖 YTD + 上年末基准(接口上限 800)。"""
    import requests
    r = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                     params={"param": f"{symbol},day,,,400,qfq"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    d = r.json()["data"][symbol]
    rows = d.get("qfqday") or d.get("day") or []
    if not rows:
        raise RuntimeError("腾讯无此指数日线")  # 932000/883* 就是这种
    s = pd.Series({x[0]: float(x[2]) for x in rows})  # 行: [date, open, close, high, low, ...]
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


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


def close_series(src: str, sym: str, now: pd.Timestamp) -> pd.Series:
    if src == "tx":
        return tencent_close(sym)
    if src == "sina":
        return sina_close(sym)
    if src == "csindex":
        return csindex_close(sym, f"{now.year - 1}1201", now.strftime("%Y%m%d"))
    return ths_close(sym, [now.year - 1, now.year])


def ytd_item(name: str, src: str, sym: str, now: pd.Timestamp, jan1: pd.Timestamp) -> dict:
    """单指数 YTD。历史深度不够(如腾讯的 bj899050 只给 1 根)同样抛错, 好让调用方换源。"""
    s = close_series(src, sym, now)
    prev_year = s[s.index < jan1]
    cur = s[s.index >= jan1]
    if prev_year.empty or cur.empty:
        raise RuntimeError(f"日线不覆盖上年末基准({len(s)} 根)")
    return {"name": name,
            "ytd": round((cur.iloc[-1] / prev_year.iloc[-1] - 1) * 100, 2),
            "close": round(float(cur.iloc[-1]), 2),
            "date": cur.index[-1].strftime("%Y-%m-%d"),
            "src": src}


def last_good(out: Path) -> dict:
    """上次导出的 {指数名: 条目}, 供单指数失败时降级复用。"""
    try:
        t = out.read_text(encoding="utf-8")
        blob = json.loads(t[t.index("=") + 1:t.rindex(";")])
        return {x["name"]: x for x in blob["items"]}
    except Exception:
        return {}


def main():
    now = pd.Timestamp.now(tz="Asia/Shanghai")
    jan1 = pd.Timestamp(f"{now.year}-01-01")
    out = Path(__file__).parent / "idx_data.js"
    old = last_good(out)
    items = []
    for name, srcs, sym in INDICES:
        item, err = None, None
        for src in srcs:
            try:
                item = ytd_item(name, src, sym, now, jan1)
                break
            except Exception as e:
                err = f"{src} {sym}: {e}"
                print(f"  {name} {err}", flush=True)
        if item is None:
            prev = old.get(name)  # 全源失败: 用上次的值并标 stale, 好过页面无声少一根条
            if prev:
                items.append(prev | {"stale": True})
                print(f"stale {name}: 沿用 {prev['date']} 的数据", flush=True)
            else:
                print(f"skip {name}({err})", flush=True)
            continue
        items.append(item)
        print(f"{name:<6} {item['ytd']:+7.2f}%  ({item['date']}, {item['src']})", flush=True)

    if not items:
        raise SystemExit("全部指数拉取失败, 不写 idx_data.js")
    items.sort(key=lambda x: x["ytd"], reverse=True)
    payload = {"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items}
    out.write_text("window.INDEX_YTD=" + json.dumps(payload, ensure_ascii=False) + ";\n",
                   encoding="utf-8")
    print(f"已导出: {out} ({len(items)} 个指数)")


if __name__ == "__main__":
    main()
