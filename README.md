# A股全市场 · 每日涨跌幅中位数看板

用全市场约 5000 只 A 股每日涨跌幅的**中位数**观察市场真实赚钱效应——比指数更能反映"大多数股票今天涨没涨"。纯静态页面，GitHub Actions 每个交易日自动更新。

## 看板内容

- **日中位数柱状图**：全市场当日涨跌幅中位数（剔除停牌），红涨绿跌
- **累计中位数曲线**：日中位数逐日累加，今年以来趋势
- 统计卡片：最新日中位数、累计中位数、上涨家数占比、红盘天数

## 快速开始

```bash
pip install baostock pandas pyarrow

python median_trend.py    # 首次全量拉取 ~10-20 分钟（断点续传），之后秒级
open index.html           # 纯静态，无需服务器
```

常用命令：

```bash
python median_trend.py --update    # 收盘后增量更新（重拉最近10天窗口去重合并）
python median_trend.py --refresh   # 删缓存全量重拉
```

## 工作原理

```
baostock 逐股日线涨跌幅
  → cache/daily_pctchg.parquet   (长表缓存，断点续传)
  → 按日聚合中位数
  → data.js                      (window.MEDIAN_DATA)
  → index.html                   (ECharts 渲染)
```

- 数据源 [baostock](http://baostock.com)，免费无需 token；当日数据约 17:30 后可用
- 样本范围：沪主板/科创板（sh.6）、深主板（sz.0）、创业板（sz.30），不含北交所和 B 股
- 停牌日剔除；非交易日自动跳过
- 只展示今年以来数据

## 自动更新

`.github/workflows/update.yml`：每个工作日北京时间 18:30 运行 `--update`，提交 `data.js` 和 parquet 缓存。也可在 Actions 页手动触发。

## 附带脚本

`intraday_etf.py`：ETF 日内时段分析，用新浪 5 分钟 bar 找跨天稳定的日内低点/高点时段。

```bash
pip install akshare
python intraday_etf.py 159696 5
```

## 免责声明

数据仅供研究参考，不构成投资建议。
