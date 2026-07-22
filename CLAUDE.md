# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

A股全市场每日涨跌幅中位数看板。数据管道：baostock 逐股拉日线涨跌幅 → 缓存到 `cache/daily_pctchg.parquet`（长表 date/code/pct，断点续传）→ 按日聚合中位数 → 导出 `data.js`（`window.MEDIAN_DATA = {...}`）→ `index.html` 用 ECharts（CDN）纯静态渲染。无构建、无测试框架、无后端。

## 常用命令

```bash
source .venv/bin/activate          # Python 3.14 venv，依赖: baostock pandas pyarrow akshare
python median_trend.py             # 有缓存则直接导出 data.js；无缓存全量拉(~10-20分钟)
python median_trend.py --update    # 收盘后增量: 腾讯批量快照拉当日(~1.5s)，缺口时回退 baostock 窗口
python test_tencent_parse.py       # 快照解析 + baostock 降级 自检(离线)
python test_nq_overnight.py        # NQ 隔夜窗口逻辑自检(离线，合成 bar)
python median_trend.py --refresh   # 删缓存全量重拉
python intraday_etf.py 159696 5    # 独立脚本: ETF 日内时段分析(新浪5min bar)
open index.html                    # 看结果，无需服务器
```

## 关键约束与坑

- **CI 时区是 UTC**：GitHub Actions（`.github/workflows/update.yml`）每个工作日跑两次 `--update` 并提交 `data.js` + parquet——UTC 07:10（北京 15:10，收盘后 10 分钟，腾讯快照即时可用）为主，UTC 10:00（北京 18:00）兜底（万一 15:10 落到 baostock 回退分支，那时 baostock 当日数据还没出）。任何面向展示的时间必须用 `pd.Timestamp.now(tz="Asia/Shanghai")`，裸 `now()` 在 CI 里是零时区。
- **`--update` 主源是腾讯批量快照**（`qt.gtimg.cn/q=` 逗号拼代码，每请求实测上限 900 只、取 800，全市场 7 请求 ~1.5s；字段 `[6]`成交量 `[30]`时间戳 `[32]`涨跌幅%，GBK 编码）。三条硬约束：① 停牌股返回 `pct=0.00` 而非空，必须按 `成交量>0` 剔，否则中位数被 0 拉偏；② 校验 `[30]` 时间戳日期等于目标日，防隔日跑写进陈旧价；③ **未收盘不落库**——盘中快照会被当成收盘价写死，次日 `--update` 只取新一天不回补。与 baostock qfq 口径实测 30 股 0 偏差。
- **回退 baostock 的两种情形**：缓存缺了 end 之前的交易日（腾讯只给当日，补不了），或腾讯覆盖率 <90%/请求异常。此时才走原来的逐股窗口重拉（慢，且 **baostock 当日数据 ~17:30 后才可用**，cron 时间是配合这个定的）。腾讯 15:00 收盘即出，若哪天想把 cron 提前，得先确认不会落到回退分支。
- **baostock 挂了也要能出数（`update_incremental()`）**：2026-07-22 实际发生过——baostock 全站不可用，login 卡 2 分多钟后抛错，而当时数据主源已经是腾讯，却被日历/代码表这两个辅助调用整条拖死。现在 login 失败即降级：代码表退回 parquet 里最近交易日那份（漏掉当天新上市的几只，5000 只样本的中位数无感），交易日判断交给腾讯快照自己——节假日快照返回上个交易日的陈旧时间戳，`parse_tencent_quotes` 的日期校验会全过滤掉，覆盖率 0 自然不写。此时**不再回退 baostock**（它本来就挂着），直接不更新，缺口留给它恢复后的兜底跑补。改这块前先跑 `test_tencent_parse.py`（覆盖正常/缺口/非交易日/降级出数/降级不写五条分支，全离线）。
- **A股代码过滤**：`A_PREFIXES = ("sh.6", "sz.0", "sz.30")`。必须用 `sz.30` 而非 `sz.3`，否则 `sz.399*` 深证指数混入。不含北交所和B股。
- **停牌剔除**：baostock 停牌日 `pctChg` 为空字符串，拉取时已过滤。
- **非交易日保护**：`--update` 先走 `trading_days()`，节假日空跑不写脏数据（`is_trading_day()` 是它的单日包装，`intraday_cache.py` 在用）。
- **baostock 会话**：`bs_session()` 是可嵌套的全局单例，login 握手要 1 秒多，别再在函数里各 login 一次；`--update` 全流程共用一次登录（29s → 15s）。
- **ST 名单缓存**：`st_codes()` 结果写 `cache/st_codes.json` 存 7 天并入库。`query_stock_basic()` 拉全市场 5000+ 行是本脚本最慢的单次调用，而戴帽摘帽是低频事件。拉到空名单时抛错不覆盖旧缓存。
- **写盘前体检**：`sanity_check()` 在写 `data.js` 前断言列长一致/日期升序无重复/无未来日期/中位数 `<15%`/上涨占比 `0~100`/最新日样本 `>4000`。失败即抛，CI 那步红掉就不会 commit——脏数据一旦入库，次日 `--update` 只取新一天不会回补。故意不查新鲜度：节假日本就没新数据，没写就没 commit。
- **CI push 竞态**：workflow 里 push 前 `git pull --rebase`，改 workflow 时保留。
- **cache/ 的 gitignore 特殊规则**：`cache/*` 被忽略但 `daily_pctchg.parquet`、`intraday_159696_1min.parquet`、`nq_min.parquet`、`st_codes.json` 例外（CI 增量依赖它们入库）。新增缓存文件要同时改 `.gitignore` 和 workflow 的 `git add`。
- **159696 分时累积**：`intraday_cache.py` 每日 CI 拉新浪 1min bar（固定最新 1970 根 ≈9 交易日），按 `day` 去重合并进 parquet，逐日累加突破 1970 根限制。新浪偶发失败时 `continue-on-error` 不阻断中位数更新。合并后导出 `etf_data.js`（日K聚合 + 每日分时，剔除不足 200 根的边界日），`etf.html` 静态渲染（点击K线看分时），CI 一并提交。
- **指数 YTD 卡片**：`index_perf.py` 拉 13 个宽基/特色指数今年以来涨跌幅 → 导出 `idx_data.js`，`index.html` 排名条形图卡片渲染。源按序回退：腾讯 `fqkline`（10 个沪深指数，一次请求 400 根日线，与新浪逐日实测完全一致）→ 新浪 `stock_zh_index_daily`（akshare 封装随网站变，故降为备源；北证50 `bj899050` 腾讯只给 1 根，实际就靠它）；中证2000 `932000` 腾讯/新浪都没有，只能走中证官网 `stock_zh_index_hist_csindex`；微盘股 883418 / 可转债 883981 是同花顺自编指数，直连 `d.10jqka.com.cn/v4/line/bk_*` 接口带 ths.js 算的 v cookie。「拉到了但历史不覆盖上年末基准」也算失败并换源。全源失败则复用上次 `idx_data.js` 里那条并标 `stale:true`，前端半透明 + tooltip 注明——比静默少一根条可见。CI `continue-on-error` 单独跑。
- **纳指期货隔夜卡片**：`nq_overnight.py` 拉新浪外盘 NQ 分时（`GlobalFuturesService.getGlobalFuturesMinLine`，只返回当前一个盘 ~1380 根 1min），按 dt 去重累积进 `cache/nq_min.parquet`，算「前一 A 股交易日 15:00 → 当日 9:30（北京）」涨跌幅，末尾附半程点（最后交易日 15:00 → 最新 bar，`partial:true`，前端半透明柱 +「截至」标注，次日被完整点替代）→ 导出 `nq_data.js`，`etf.html` 顶部柱状卡片渲染（无点位自动隐藏）。半程点盘中陈旧：`etf.html` 客户端直连新浪 MinLine（`<script>` 注入绕 CORS，`stock2` 接口无防盗链）开页自刷 + 每 8s 自动轮询（base 固定只重算实时价）；过了 A 股 15:00 前端 `nqRebase()` 自己换窗口（拿实时 bar 里当日 15:00 价当新基准，同 py 端 `crossed` 逻辑），否则 15:00~18:00 曲线卡在旧窗口右边界，故不再需要开盘前 CI 跑（8:45 那个已删；但见下方 `nq_night.yml`，那个是补历史 bar 的，别混淆）。新浪外盘 NQ MinLine 实测延迟约 1 分钟（当前分钟 bar 形成中），非早前标注的 10 分钟；海外期货无免费逐笔源，1min bar 已是最快。交易日历取 `daily_pctchg.parquet` 日期列，CME 假日晨盘无 bar 自动跳过该日。CI `continue-on-error` 单独跑。注意 MinLine 每天 6:00 切新盘，「昨 18:00→今 5:00」的 bar 只能在切盘前抓：`nq_night.yml` cron UTC 21:00（北京 5:00）专补这段——NQ 每日 5:00 收盘，5:00~6:00 抓到的必是刚收完的完整整夜盘，天然容忍 Actions 常见的 10~50 分钟 cron 延迟（落地窗口有整整 1 小时）。少了它分时曲线会从上次抓取时刻断到次日 6:00（指标只需两端点，断的只是曲线）。前端 `nqClosed()` 把 CME 闭市时段（每日 5:00-6:00 维护、周六 6:00→周一 6:00）从类目轴剔除，不留空槽。东财 push2his 备选源已试过，对非浏览器请求限流断连，弃用。改 `overnight()` 前先跑 `test_nq_overnight.py`（合成 bar，覆盖完整点 / CME 假日跳过 / 过 15:00 换窗 / 周末不换窗四条分支）。
- 只导出今年以来的数据（`main()` 末尾按 `%Y-01-01` 过滤）。
