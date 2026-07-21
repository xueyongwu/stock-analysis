"""NQ 隔夜窗口逻辑自检(合成 bar, 不联网): python test_nq_overnight.py

覆盖 overnight() 的四条分支: 完整点、CME 假日跳过、过 15:00 换窗、周末不换窗。
"""
import pandas as pd

from nq_overnight import overnight

MON, TUE, WED, SAT = (pd.Timestamp(f"2026-07-{d}") for d in (20, 21, 22, 25))


def bars(*pairs):
    """[("2026-07-20 15:00", 100.0), ...] -> overnight() 要的 DataFrame。"""
    return pd.DataFrame({"dt": pd.to_datetime([t for t, _ in pairs]),
                         "price": [p for _, p in pairs]})


def test_complete_point():
    """前一交易日 15:00 -> 当日 9:30, 取窗口内最后一根。"""
    items = overnight(bars(("2026-07-20 15:00", 100.0),
                           ("2026-07-21 06:00", 100.5),
                           ("2026-07-21 09:30", 101.0)), [MON, TUE])
    assert len(items) == 1, items
    it = items[0]
    assert (it["d"], it["base"], it["open"], it["pct"]) == ("2026-07-21", 100.0, 101.0, 1.0), it
    assert "partial" not in it, it
    assert it["path"][0] == ["07-20 15:00", 0.0], it["path"]      # 基准点归零
    assert it["path"][-1] == ["07-21 09:30", 1.0], it["path"]


def test_cme_holiday_skipped():
    """当日晨间无 bar(美国假日) -> 该日不出点, 不影响其它日。"""
    items = overnight(bars(("2026-07-20 15:00", 100.0),
                           ("2026-07-21 09:30", 101.0)), [MON, TUE, WED])
    assert [it["d"] for it in items] == ["2026-07-21"], items


def test_rebase_after_1500():
    """已越过更晚的 A 股 15:00 -> 半程点用新基准(不再挂在旧窗口右边界)。"""
    items = overnight(bars(("2026-07-20 15:00", 100.0),
                           ("2026-07-21 15:00", 102.0),
                           ("2026-07-21 16:00", 103.0)), [MON])
    assert len(items) == 1, items
    it = items[0]
    assert it["partial"] is True and it["base"] == 102.0, it   # 102 而非 100
    assert (it["pct"], it["t"]) == (0.98, "2026-07-21 16:00"), it


def test_weekend_not_rebase():
    """周末的 15:00 不是 A 股收盘, 不能当新基准。"""
    items = overnight(bars(("2026-07-20 15:00", 100.0),
                           ("2026-07-25 15:00", 110.0),
                           ("2026-07-25 16:00", 111.0)), [MON])
    assert len(items) == 1 and items[0]["base"] == 100.0, items
    assert items[0]["pct"] == 11.0, items


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


if __name__ == "__main__":
    main()
