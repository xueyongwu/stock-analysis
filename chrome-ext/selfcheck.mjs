// node chrome-ext/selfcheck.mjs — 离线自检 sw.js 的两个纯函数(解析 + badge 格式化)
import { strict as a } from "node:assert";
import { badge, jsonIn } from "./sw.js";

// jsonIn: 两个数据源各自的包裹
a.deepEqual(jsonIn('window.NQ_OVERNIGHT={"items":[{"pct":0.3}]};\n'), { items: [{ pct: 0.3 }] });
a.deepEqual(
  jsonIn(`/*<script>location.href='//sina.com';</script>*/\nvar t=({"minLine_1d":[["06:00","1.5"]]});`),
  { minLine_1d: [["06:00", "1.5"]] },
);

// badge: 4 字符预算, 无符号(方向靠底色)
a.equal(badge(0.32), "0.32");
a.equal(badge(-0.14), "0.14");
a.equal(badge(-1.5), "1.50");
a.equal(badge(12.345), "12.3");
a.equal(badge(-123.4), "123");
a.equal(badge(-0.001), "0.00");  // 归零不能漏出负号, 底色由 +s===0 判灰

console.log("ok");
