"""ETF 盘中时段分析: 找日内系统性买卖点。

数据源: 新浪 5min bar (akshare stock_zh_a_minute), 固定返回最新 1970 根 ≈ 43 交易日。
东财分时接口当前限流全断, 故用新浪。

思路: 每根 bar 按"日内时刻"(HH:MM)归类, 跨天算该时刻相对当日均价的偏离,
      平均后得到"典型日内曲线": 偏离持续为负的时刻=系统性低点(买), 持续为正=高点(卖)。
      叠加各时刻平均分段收益, 辅证。

用法: python intraday_etf.py [代码] [周期]
      python intraday_etf.py 159696 5     # 默认
依赖: akshare pandas
"""
import sys
import pandas as pd


def load(code: str, period: str = "5") -> pd.DataFrame:
    import akshare as ak
    mkt = "sh" if code[0] == "6" else "sz"
    df = ak.stock_zh_a_minute(symbol=f"{mkt}{code}", period=period, adjust="")
    df["day"] = pd.to_datetime(df["day"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["date"] = df["day"].dt.date
    df["tod"] = df["day"].dt.strftime("%H:%M")   # 日内时刻
    return df


def analyze(df: pd.DataFrame) -> pd.DataFrame:
    """每时刻: 相对当日均价的偏离(%) 跨天均值 + 该时刻平均成交量占比。"""
    # 每天用当日全天均价(VWAP 近似: close 均值)做基准, 消除日间趋势
    day_mean = df.groupby("date")["close"].transform("mean")
    df = df.assign(dev=(df["close"] / day_mean - 1) * 100)
    g = df.groupby("tod")
    out = pd.DataFrame({
        "dev_mean": g["dev"].mean().round(3),    # 平均偏离% (负=系统性低, 买点候选)
        "dev_std": g["dev"].std().round(3),      # 波动(越小越可靠)
        "vol_share": (g["volume"].mean() / df.groupby("date")["volume"].sum().mean() * 100).round(2),
        "n": g["dev"].size(),
    }).sort_index()
    return out


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "159696"
    period = sys.argv[2] if len(sys.argv) > 2 else "5"
    df = load(code, period)
    days = df["date"].nunique()
    res = analyze(df)

    print(f"{code}  {period}min  {days}交易日  {df['date'].min()}~{df['date'].max()}\n")
    print("=== 日内典型偏离曲线 (dev_mean<0=系统性低=买点候选, >0=高=卖点候选) ===")
    print(res.to_string())

    buy = res.nsmallest(3, "dev_mean")
    sell = res.nlargest(3, "dev_mean")
    print(f"\n最佳买点时段(最低3): {list(buy.index)}  偏离 {buy['dev_mean'].tolist()}%")
    print(f"最佳卖点时段(最高3): {list(sell.index)}  偏离 {sell['dev_mean'].tolist()}%")
    print(f"\n提示: 样本仅 {days} 天, 非统计显著; dev_std 大的时刻不可靠。仅趋势参考。")


if __name__ == "__main__":
    main()
