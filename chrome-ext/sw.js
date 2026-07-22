// 纳指期货隔夜涨跌 badge。
// base(上一 A 股 15:00 收盘时的 NQ 价)取 CI 产出的 nq_data.js, 实时价取新浪外盘 MinLine,
// pct = 实时价/base - 1。同 etf.html 里半程点的算法。
// 不移植 etf.html 的 nqRebase(): CI 15:10(北京) 那趟已把新窗口写进 nq_data.js,
// 而 alarm 粒度 1 分钟, 抢不到那 10 分钟。
const NQ_DATA = "https://raw.githubusercontent.com/xueyongwu/stock-analysis/main/nq_data.js";
const MINLINE = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/GlobalFuturesService.getGlobalFuturesMinLine?symbol=NQ";
const PAGE = "https://xueyongwu.github.io/stock-analysis/etf.html";
const UP = "#e4534a", DOWN = "#16b070", FLAT = "#8a8a8a";  // A股惯例: 涨红跌绿, 同 index.html

// 两个接口都不是纯 JSON: 一个是 "window.NQ_OVERNIGHT={...};",
// 一个是 JSONP 且带 "/*<script>location.href='//sina.com';</script>*/" 防盗链前缀。
// 前缀里都不含花括号, 按首尾花括号切即可。
export const jsonIn = t => JSON.parse(t.slice(t.indexOf("{"), t.lastIndexOf("}") + 1));

// badge 只放得下约 4 字符。不带符号(涨跌看底色), 按量级降精度: "0.74" / "12.3" / "123"
export const badge = p => {
  const v = Math.abs(p);
  return v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : v.toFixed(0);
};

const get = async url => jsonIn(await (await fetch(url, { cache: "no-store" })).text());

async function refresh() {
  try {
    const items = (await get(NQ_DATA)).items;
    const last = items[items.length - 1];
    let { pct, t } = last;
    if (last.partial) {  // 进行中: base 固定, 只按最新 bar 重算实时价
      const bars = (await get(MINLINE)).minLine_1d;
      const b = bars[bars.length - 1];  // 首行比常规行多 4 个前缀字段, 统一从尾部取
      pct = +((+b[b.length - 5] / last.base - 1) * 100).toFixed(2);
      t = b[b.length - 1].slice(0, 16);
    }
    const s = badge(pct);
    await chrome.action.setBadgeText({ text: s });
    // 舍入后归零走灰: 不带符号时 "0.00" 配红/绿会误读成有方向
    await chrome.action.setBadgeBackgroundColor({ color: +s === 0 ? FLAT : pct > 0 ? UP : DOWN });
    await chrome.action.setTitle({
      title: `NQ 隔夜 ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%\n更新于 ${(t || last.d).slice(5)}`,
    });
  } catch (e) {
    // ponytail: 取数失败保留上次 badge 不清空 — 陈旧数字比空白有用, 详情写进 title
    await chrome.action.setTitle({ title: "NQ 隔夜 取数失败: " + e.message });
  }
}

if (globalThis.chrome?.alarms) {  // 装扩展时才注册, 好让 selfcheck.mjs 能在 node 里 import
  chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create("nq", { periodInMinutes: 1 });  // MV3 service worker 会被回收, 不能用 setInterval
    refresh();
  });
  chrome.runtime.onStartup.addListener(refresh);  // alarm 跨重启存活但要等满一周期, 这里立刻补一次
  chrome.alarms.onAlarm.addListener(refresh);
  chrome.action.onClicked.addListener(() => chrome.tabs.create({ url: PAGE }));
}
