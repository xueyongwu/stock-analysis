"""腾讯快照解析 + baostock 不可用降级 自检: python test_tencent_parse.py"""
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

import median_trend as mt
from median_trend import parse_tencent_quotes


def line(sym, *, vol="1096087", stamp="20260721161447", pct="-2.08"):
    f = ["1", "浦发银行", sym[2:], "8.95", "9.14", "9.12", vol] + ["0"] * 23
    f += [stamp, "-0.19", pct] + ["0"] * 20      # [30]时间戳 [31]涨跌额 [32]涨跌幅
    return f'v_{sym}="' + "~".join(f) + '"'


SYM2CODE = {"sh600000": "sh.600000", "sz000001": "sz.000001", "sz300750": "sz.300750"}


def with_dead_baostock(tencent_result, body):
    """在「baostock 全挂 + 缓存里有昨日数据」的环境下跑 body(), 返回其结果。"""
    def boom(*a, **k):
        raise RuntimeError("baostock login failed: 网络接收错误。")

    orig = (mt.RAW, mt.bs_session, mt.update_today_tencent, mt.fetch_history)
    seen = {}
    with tempfile.TemporaryDirectory() as tmp:
        mt.RAW = Path(tmp) / "raw.parquet"
        pd.DataFrame({"date": pd.Timestamp("2026-07-21"),
                      "code": ["sh.600000", "sz.000001"], "pct": [1.0, -1.0]}).to_parquet(mt.RAW)
        mt.bs_session = boom     # login 就失败, 日历/代码表都拿不到
        mt.fetch_history = boom  # baostock 挂了就不该再走回退拉取
        def fake_tencent(codes, day):
            seen["codes"] = codes
            return tencent_result

        mt.update_today_tencent = fake_tencent
        try:
            return body(), seen
        finally:
            (mt.RAW, mt.bs_session, mt.update_today_tencent, mt.fetch_history) = orig


def with_live_baostock(days, tencent_result, body):
    """baostock 正常的环境: 日历返回 days, 代码表返回两只, 缓存里只有 07-21。"""
    @contextmanager
    def ok_session():
        yield None

    orig = (mt.RAW, mt.bs_session, mt.trading_days, mt.all_a_codes,
            mt.update_today_tencent, mt.fetch_history)
    seen = {}
    with tempfile.TemporaryDirectory() as tmp:
        mt.RAW = Path(tmp) / "raw.parquet"
        pd.DataFrame({"date": pd.Timestamp("2026-07-21"),
                      "code": ["sh.600000", "sz.000001"], "pct": [1.0, -1.0]}).to_parquet(mt.RAW)
        mt.bs_session = ok_session
        mt.trading_days = lambda start, end: days
        mt.all_a_codes = lambda day: ["sh.600000", "sz.000001", "sh.600004"]  # 含当天新股

        def fake_tencent(codes, day):
            seen["tencent"] = codes
            return tencent_result

        def fake_fetch(codes, start, end, skip_done=True):
            seen["baostock_window"] = (start, end)
            return "from_baostock"

        mt.update_today_tencent, mt.fetch_history = fake_tencent, fake_fetch
        try:
            return body(), seen
        finally:
            (mt.RAW, mt.bs_session, mt.trading_days, mt.all_a_codes,
             mt.update_today_tencent, mt.fetch_history) = orig


def test_normal_path_uses_baostock_codes():
    """日历齐、无缺口: 走腾讯, 代码表用 baostock 的(含当天新股)。"""
    snap = pd.DataFrame({"date": pd.Timestamp("2026-07-22"), "code": ["sh.600000"], "pct": [2.0]})
    got, seen = with_live_baostock(["2026-07-21", "2026-07-22"], snap,
                                   lambda: mt.update_incremental("2026-07-22"))
    assert got is snap and "baostock_window" not in seen, seen
    assert seen["tencent"] == ["sh.600000", "sz.000001", "sh.600004"], seen


def test_gap_falls_back_to_baostock():
    """缓存缺了 end 之前的交易日: 腾讯补不了, 必须走 baostock 窗口。"""
    got, seen = with_live_baostock(["2026-07-20", "2026-07-21", "2026-07-22"], None,
                                   lambda: mt.update_incremental("2026-07-22"))
    assert got == "from_baostock", got
    assert "tencent" not in seen and seen["baostock_window"][1] == "2026-07-22", seen


def test_non_trading_day_skipped():
    """end 不在交易日历里: 直接不写。"""
    got, seen = with_live_baostock(["2026-07-21"], None,
                                   lambda: mt.update_incremental("2026-07-22"))
    assert got is None and seen == {}, (got, seen)


def test_degrade_to_cached_codes():
    """baostock 挂了: 代码表退回缓存, 腾讯照常出数。"""
    snap = pd.DataFrame({"date": pd.Timestamp("2026-07-22"),
                         "code": ["sh.600000"], "pct": [2.0]})
    got, seen = with_dead_baostock(snap, lambda: mt.update_incremental("2026-07-22"))
    assert got is snap, got
    assert seen["codes"] == ["sh.600000", "sz.000001"], seen  # 取自缓存最近交易日


def test_degrade_no_quotes_writes_nothing():
    """baostock 挂 + 腾讯也没当日数据(如节假日): 不写盘, 也不去 baostock 回退。"""
    got, _ = with_dead_baostock(None, lambda: mt.update_incremental("2026-07-22"))
    assert got is None, got


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

    text = ";".join([
        line("sh600000"),
        line("sz000001", vol="0", pct="0.00"),          # 停牌: 成交量 0, pct 恒 0
        line("sz300750", stamp="20260720161447"),        # 陈旧: 非当日快照
        line("sh601398"),                                # 不在本批 code 表内
    ])
    got = parse_tencent_quotes(text, "20260721", SYM2CODE)
    assert got == {"sh.600000": -2.08}, got

    assert parse_tencent_quotes('v_sh600000="1~名~600000~8.95"', "20260721", SYM2CODE) == {}  # 字段截断
    assert parse_tencent_quotes("", "20260721", SYM2CODE) == {}
    print("ok")


if __name__ == "__main__":
    main()
