const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const elements = new Map();
const storage = new Map();
let soundStarts = 0;
class FakeElement {
  constructor() {
    this.value = "";
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.selectors = new Map();
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(n => classes.add(n)),
      remove: (...names) => names.forEach(n => classes.delete(n)),
      contains: name => classes.has(name),
      toggle: (name, value) => value ? classes.add(name) : classes.delete(name),
    };
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new FakeElement());
    return this.selectors.get(selector);
  }
  querySelectorAll() { return []; }
  prepend(child) { child.parent = this; child.isConnected = true; this.children.unshift(child); }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this);
    this.isConnected = false;
  }
  replaceWith(child) {
    const index = this.parent.children.indexOf(this);
    this.parent.children[index] = child;
    child.parent = this.parent;
    child.isConnected = true;
    this.isConnected = false;
  }
}
function element(id) {
  if (!elements.has(id)) elements.set(id, new FakeElement());
  return elements.get(id);
}
const context = vm.createContext({
  document: { getElementById: element, addEventListener() {}, createElement: () => new FakeElement() },
  window: { AudioContext: class {
    state = "running";
    currentTime = 0;
    createOscillator() { return { frequency: {}, connect() {}, start() { soundStarts++; }, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
  }},
  localStorage: { getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v) },
  location: { protocol: "http:", host: "127.0.0.1:8765" },
  WebSocket: class { addEventListener() {} },
  setTimeout: () => 1, setInterval: () => 1, clearInterval() {}, clearTimeout() {}, console,
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/assets/app.js"), "utf8"), context);
const run = (code) => vm.runInContext(code, context);

assert.ok(Number.isNaN(run("getCost()")), "empty cost must not become zero");
element("costInput").value = "0";
assert.equal(run("getCost()"), 0);
element("costInput").value = "-1";
assert.ok(Number.isNaN(run("getCost()")));
assert.equal(run("formatCount(null)"), "未采集");
assert.equal(run("formatCount(0)"), "0");
assert.equal(run("salesOf({sales_by_size: {'39': {sales_7d: 100}}}, '39').d7"), null);
assert.equal(run("salesOf({sales_source: 'merchant_7d', sales_by_size: {'39': {sales_7d: 0}}}, '39').d7"), 0);
element("minProfit").value = "30";
element("minSales7d").value = "0";
const missing = "buildDecisionRows({size_prices:[{size:'39',price:404.22}]}, 300)[0]";
assert.equal(run(`${missing}.buy`), false, "missing sales cannot produce a buy recommendation");
assert.equal(run(`${missing}.reason`), "销量未采集");
const verified = "{sales_source:'merchant_7d',sales_by_size:{'39':{sales_7d:23}},size_prices:[{size:'39',price:404.22}]}";
assert.equal(run(`buildDecisionRows(${verified},300)[0].buy`), true);
assert.equal(run(`buildDecisionRows({...${verified},pending_sales:true},300)[0].buy`), false);
assert.equal(run(`buildDecisionRows({...${verified},pending_sales:true},300)[0].label`), "查询中");
assert.match(run(`renderDecision({...${verified},pending_sales:true},300)`), /等待商家销量，暂不生成建议/);
assert.equal(run(`buildDecisionRows(${verified},NaN)[0].label`), "待填成本");
assert.match(run(`renderDecision(${verified},NaN)`), /待填成本/);
assert.doesNotMatch(run(`renderDecision(${verified},NaN)`), /观望|建议买/);
assert.equal(run(`buildDecisionRows(${verified},-1)[0].buy`), false);
for (const invalid of ["null", "''", "'无报价'", "Infinity", "-1", "true"]) {
  assert.equal(run(`buildDecisionRows({...${verified},size_prices:[{size:'39',price:${invalid}}]},300)[0].income`), null);
}
assert.equal(run(`buildDecisionRows({...${verified},size_prices:[{size:'39',price:'无报价'}]},NaN)[0].label`), "待填成本");
assert.equal(run(`buildDecisionRows({...${verified},size_prices:[{size:'39',price:'无报价'}]},300)[0].label`), "收入缺失");
for (const invalid of ["null", "''", "'未知'", "Infinity", "-1", "1.5", "true"]) {
  assert.equal(run(`salesOf({sales_source:'merchant_7d',sales_by_size:{'39':{sales_7d:${invalid}}}},'39').d7`), null);
}
element("minSales7d").value = "10";
const lowSales = `{...${verified},sales_by_size:{'39':{sales_7d:2}}}`;
assert.equal(run(`buildDecisionRows(${lowSales},300)[0].state`), "sales_low");
assert.match(run(`renderDecision(${lowSales},300)`), /7日销量 2，低于最低销量 10/);
assert.match(run(`renderDecision(${lowSales},300)`), /销量不足/);
assert.equal(run(`buildDecisionRows(${verified},400)[0].state`), "profit_low");
assert.match(run(`buildDecisionRows(${verified},400)[0].reason`), /低于最低利润/);
assert.match(run("renderDecision(missingData = {size_prices:[{size:'39',price:404.22}]},300)"), /销量缺失，暂不能判断/);
assert.doesNotMatch(run("renderDecision(missingData,300)"), /观望/);
const boundary = "{sales_source:'merchant_7d',sales_by_size:{'39':{sales_7d:10}},size_prices:[{size:'39',price:263.46}]}";
assert.equal(run(`buildDecisionRows(${boundary},233.46)[0].profit`), 30);
assert.equal(run(`buildDecisionRows(${boundary},233.46)[0].buy`), true, "exact profit/sales thresholds must qualify despite floating-point rounding");
assert.equal(run(`buildDecisionRows(${boundary},0)[0].buy`), true);

for (let index = 1; index <= 4; index++) {
  run(`prependResult({job_id:'job-${index}',query:'SKU-${index}',ok:true,size_prices:[]})`);
}
assert.equal(run("resultHistory.length"), 3);
assert.equal(run("resultCards.size"), 3);
assert.equal(run("resultHistory.map(entry => entry.id).join(',')"), "job-4,job-3,job-2");
assert.equal(element("results").children.length, 3);
assert.equal(element("results").children.filter(card => !card.hidden).length, 1);
run("selectResult('job-3')");
assert.equal(run("selectedResultId"), "job-3");
run("prependResult({job_id:'job-4',query:'SKU-4',ok:true,size_prices:[],pending_sales:false})");
assert.equal(run("selectedResultId"), "job-3", "final update must not steal history selection");
assert.equal(element("results").children.length, 3, "partial and final use one record");
assert.equal(element("resultPrev").disabled, false);
assert.equal(element("resultNext").disabled, false);
element("resultNext").listeners.click();
assert.equal(run("selectedResultId"), "job-2");
assert.equal(element("resultNext").disabled, true);
element("resultPrev").listeners.click();
assert.equal(run("selectedResultId"), "job-3");

run("var many = {size_prices:Array.from({length:13},(_,i)=>({size:String(36+i),price:400+i}))}; var page={page:0}");
element("costInput").value = "";
let markup = run("renderDecision(many,NaN,page)");
assert.equal((markup.match(/<tr class=/g) || []).length, 8);
assert.match(markup, /1–8 \/ 13/);
run("page.page=1");
markup = run("renderDecision(many,NaN,page)");
assert.equal((markup.match(/<tr class=/g) || []).length, 5);
assert.match(markup, /9–13 \/ 13/);
run("renderDecision(many,500,page)");
assert.equal(run("page.page"), 0, "filter changes clamp the page");
assert.equal(element("soundToggle").attributes["aria-checked"], "false");
element("soundToggle").listeners.click();
assert.equal(element("soundToggle").attributes["aria-checked"], "true");
assert.equal(storage.get("dewu-completion-sound"), "true");
soundStarts = 0;
run("handleEvent('job_started',{job_id:'dedup',query:'SKU',query_type:'sku'})");
assert.equal(element("taskStatus").dataset.state, "running");
assert.equal(element("taskStatus").hidden, false);
run("handleEvent('result',{job_id:'dedup',query:'SKU',ok:true,size_prices:[],elapsed_ms:5000})");
run("handleEvent('result',{job_id:'dedup',query:'SKU',ok:true,size_prices:[],elapsed_ms:5000})");
assert.equal(soundStarts, 2, "one two-note chime despite HTTP/WS duplicate");
assert.equal(element("completionToast").hidden, false);
assert.equal(element("taskStatus").dataset.state, "success");
assert.equal(element("taskStatus").hidden, true);
assert.equal(element("taskTitle").textContent, "查询完成");
run("finishTask({ok:true,query:'SKU',warnings:['销量缺失'],size_prices:[],elapsed_ms:1000})");
assert.equal(element("taskStatus").dataset.state, "warning");
assert.equal(element("taskStatus").hidden, true);
run("finishTask({ok:false,message:'用户已终止查询',elapsed_ms:1000})");
assert.equal(element("taskStatus").dataset.state, "warning");
assert.equal(element("toastView").hidden, true);
assert.equal(element("taskStatus").hidden, true);
run("finishTask({ok:false,message:'网络错误',elapsed_ms:1000})");
assert.equal(element("taskStatus").hidden, true);
assert.equal(element("completionToast").hidden, false, "failure notifications must remain available");
run("prependResult({job_id:'photo-test',query:'SKU',ok:true,product_image:'shoe product.png',size_prices:[]})");
const photoMarkup = run("resultCards.get('photo-test').innerHTML");
assert.match(photoMarkup, /shoe%20product\.png/);
assert.ok(photoMarkup.indexOf("data-product-image") < photoMarkup.indexOf('class="price"'));
run("prependResult({job_id:'no-photo',query:'SKU',ok:true,size_prices:[]})");
assert.doesNotMatch(run("resultCards.get('no-photo').innerHTML"), /data-product-image/);
run("beginTask('NEXT-SKU')");
assert.equal(element("taskStatus").hidden, false, "next query must show progress again");
run("finishTask({ok:true,query:'NEXT-SKU',size_prices:[],elapsed_ms:1000})");
element("clearResultsBtn").listeners.click();
assert.equal(run("resultHistory.length"), 0);
assert.equal(run("resultCards.size"), 0);
assert.equal(element("resultNav").hidden, true);
console.log("Frontend tests passed: decisions, three-result retention, paging, completion status, sound and deduplication.");
