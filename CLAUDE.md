# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

A股全市场每日涨跌幅中位数看板。数据管道：baostock 逐股拉日线涨跌幅 → 缓存到 `cache/daily_pctchg.parquet`（长表 date/code/pct，断点续传）→ 按日聚合中位数 → 导出 `data.js`（`window.MEDIAN_DATA = {...}`）→ `index.html` 用 ECharts（CDN）纯静态渲染。无构建、无测试框架、无后端。

## 常用命令

```bash
source .venv/bin/activate          # Python 3.14 venv，依赖: baostock pandas pyarrow akshare
python median_trend.py             # 有缓存则直接导出 data.js；无缓存全量拉(~10-20分钟)
python median_trend.py --update    # 收盘后增量: 重拉最近10天窗口，按(date,code)去重合并
python median_trend.py --refresh   # 删缓存全量重拉
python intraday_etf.py 159696 5    # 独立脚本: ETF 日内时段分析(新浪5min bar)
open index.html                    # 看结果，无需服务器
```

## 关键约束与坑

- **CI 时区是 UTC**：GitHub Actions（`.github/workflows/update.yml`）每个工作日 UTC 10:30（北京 18:30）跑 `--update` 并提交 `data.js` + parquet。任何面向展示的时间必须用 `pd.Timestamp.now(tz="Asia/Shanghai")`，裸 `now()` 在 CI 里是零时区。
- **baostock 当日数据 ~17:30 后才可用**，cron 时间是配合这个定的，不要提前。
- **A股代码过滤**：`A_PREFIXES = ("sh.6", "sz.0", "sz.30")`。必须用 `sz.30` 而非 `sz.3`，否则 `sz.399*` 深证指数混入。不含北交所和B股。
- **停牌剔除**：baostock 停牌日 `pctChg` 为空字符串，拉取时已过滤。
- **非交易日保护**：`--update` 先走 `is_trading_day()`，节假日空跑不写脏数据。
- **CI push 竞态**：workflow 里 push 前 `git pull --rebase`，改 workflow 时保留。
- **cache/ 的 gitignore 特殊规则**：`cache/*` 被忽略但 `!cache/daily_pctchg.parquet` 和 `!cache/intraday_159696_1min.parquet` 例外（CI 增量依赖它们入库）。
- **159696 分时累积**：`intraday_cache.py` 每日 CI 拉新浪 1min bar（固定最新 1970 根 ≈9 交易日），按 `day` 去重合并进 parquet，逐日累加突破 1970 根限制。新浪偶发失败时 `continue-on-error` 不阻断中位数更新。合并后导出 `etf_data.js`（日K聚合 + 每日分时，剔除不足 200 根的边界日），`etf.html` 静态渲染（点击K线看分时），CI 一并提交。
- **指数 YTD 卡片**：`index_perf.py` 拉 13 个宽基/特色指数今年以来涨跌幅（新浪 `stock_zh_index_daily` 为主；中证2000 走中证官网 `stock_zh_index_hist_csindex`；微盘股 883418 / 可转债 883981 是同花顺自编指数，直连 `d.10jqka.com.cn/v4/line/bk_*` 接口带 ths.js 算的 v cookie）→ 导出 `idx_data.js`，`index.html` 排名条形图卡片渲染。CI `continue-on-error` 单独跑，失败用旧数据。
- 只导出今年以来的数据（`main()` 末尾按 `%Y-01-01` 过滤）。
