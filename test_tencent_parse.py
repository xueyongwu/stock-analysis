"""腾讯快照解析自检: python test_tencent_parse.py"""
from median_trend import parse_tencent_quotes


def line(sym, *, vol="1096087", stamp="20260721161447", pct="-2.08"):
    f = ["1", "浦发银行", sym[2:], "8.95", "9.14", "9.12", vol] + ["0"] * 23
    f += [stamp, "-0.19", pct] + ["0"] * 20      # [30]时间戳 [31]涨跌额 [32]涨跌幅
    return f'v_{sym}="' + "~".join(f) + '"'


SYM2CODE = {"sh600000": "sh.600000", "sz000001": "sz.000001", "sz300750": "sz.300750"}


def main():
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
