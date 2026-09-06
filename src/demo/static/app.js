"use strict";

const $ = id => document.getElementById(id);
const labels = {idle:"空闲",running:"运行中",passed:"通过",failed:"失败",error:"环境错误",cancelled:"已取消",skipped:"跳过",not_run:"未执行",pending:"待运行",completed:"已完成",fault:"故障",stopped:"已停止"};
const phaseNames = {environment:"环境",environment_error:"异常",case_start:"用例",step:"步骤",control:"控制",controller:"响应",injecting:"注入",injected:"注入",assertion:"断言",phase:"报告",cleanup:"清理",case_result:"结果",cancel:"停止",cancelled:"停止",run_result:"整轮"};
const stateNames = {0:"空闲",1:"抽气中",2:"破真空中",3:"真空达标",4:"故障"};
const faultNames = {interlock:"抽气中开阀",sensor:"传感器失效",pressure:"负压力",timeout:"持续高压"};
const selected = new Set();
const caseElements = new Map();
let catalog = [], currentState = null, eventSeq = 0, sampleSeq = 0, samples = [], markers = [], allEvents = [], pollPromise = null;
let connected = false;
let instanceId = null;

function showNotice(message) { $("notice").textContent = message; $("notice").hidden = !message; }
function badge(element, status, label) { element.className = `badge ${status}`; element.textContent = label || labels[status] || status; }
function number(value, digits=0) { return Number(value).toLocaleString("zh-CN",{maximumFractionDigits:digits}); }
function clock(time) { return new Date(time*1000).toLocaleTimeString("zh-CN",{hour12:false}); }

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {cache:"no-store",signal:AbortSignal.timeout(5000)} : {
    method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data),signal:AbortSignal.timeout(15000)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `请求失败 (${response.status})`);
  return value;
}

async function action(path, data) {
  try {
    showNotice("");
    const value = await api(path, data);
    if (pollPromise) await pollPromise;
    await pollOnce();
    return value;
  } catch (error) { showNotice(error.message); throw error; }
}

function buildCases() {
  const list = $("caseList");
  for (const item of catalog) {
    selected.add(item.id);
    const card = document.createElement("div"); card.className = "case-card";
    const row = document.createElement("div"); row.className = "case-title-row";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = true; checkbox.id = `select-${item.id}`;
    checkbox.addEventListener("change", () => { checkbox.checked ? selected.add(item.id) : selected.delete(item.id); updateButtons(); });
    const label = document.createElement("label"); label.htmlFor = checkbox.id; label.textContent = item.title;
    const result = document.createElement("span"); result.className = "case-result"; result.textContent = "待运行";
    const description = document.createElement("div"); description.className = "case-description"; description.textContent = item.description; description.title = item.description;
    row.append(checkbox,label,result); card.append(row,description); list.append(card);
    caseElements.set(item.id,{card,checkbox,result,description});
  }
}

function updateButtons() {
  const idle = connected && currentState?.mode === "idle" && !currentState?.needs_reset;
  const active = currentState?.controller?.status === "running";
  $("runAll").disabled = !idle;
  $("runSelected").disabled = !idle || !selected.size;
  $("cancelRun").disabled = currentState?.mode !== "running";
  $("resetEnvironment").disabled = !currentState || currentState.mode !== "idle";
  $("faultSelect").disabled = !idle || !!currentState?.active_fault;
  $("injectFault").disabled = !idle || !!currentState?.active_fault;
  document.querySelectorAll("[data-action]").forEach(button => {
    button.disabled = !idle || (["pump","vent"].includes(button.dataset.action) && (active || !!currentState?.active_fault));
  });
  for (const item of caseElements.values()) item.checkbox.disabled = !idle;
}

function renderState(data) {
  currentState = data;
  const snap = data.snapshot;
  connected = snap.connected && Date.now()/1000 - snap.time < 3;
  $("connectionLed").className = `led ${connected ? "good" : "bad"}`;
  $("connectionText").textContent = connected ? data.needs_reset ? "连接已恢复 · 请重置环境" : "仿真环境已连接" : "PLC 连接中断";
  if (data.needs_reset) $("connectionLed").className="led bad";
  $("endpoint").textContent = data.endpoint || "本地独享仿真环境";
  const nodes = snap.nodes || {};
  const value = key => connected ? nodes[`vacuum.${key}`]?.value : null;
  const good = key => connected && nodes[`vacuum.${key}`]?.good;
  const pressure = value("pressure_pa");
  const valid = good("pressure_pa") && typeof pressure === "number" && Number.isFinite(pressure) && pressure > 0;
  const plcState = value("state_code"), valve = value("valve_open"), alarm = value("alarm_code");
  const scene = $("schematic");
  scene.className = "schematic";
  scene.classList.toggle("disconnected",!connected);
  scene.classList.toggle("sensor-bad",connected && !valid);
  scene.classList.toggle("valve-open",good("valve_open") && valve === true);
  scene.classList.toggle("pumping",valid && good("state_code") && plcState === 1);
  scene.classList.toggle("venting",valid && good("valve_open") && valve === true);
  $("pressureValue").textContent = !connected || pressure === null ? "—" : typeof pressure === "number" ? number(pressure,1) : pressure;
  $("pressureHint").textContent = !connected ? "数据已失效" : !valid ? "测量异常 · 等待恢复" : plcState === 3 ? "已达到目标压力" : plcState === 1 ? "抽气进行中" : plcState === 2 ? "正在恢复常压" : "当前实际压力";
  $("valveLabel").textContent = !good("valve_open") ? "未知" : valve ? "已打开" : "已关闭";
  $("pumpLabel").textContent = !good("state_code") ? "状态未知" : plcState === 1 ? "抽气中" : "未抽气";
  $("sensorLabel").textContent = !connected ? "失联" : valid ? "测量正常" : "测量异常";
  $("gasParticles").style.opacity = valid ? String(Math.max(.08,Math.min(1,pressure/101325))) : "0";
  $("gaugeNeedle").style.transform = `rotate(${valid ? Math.max(-60,Math.min(30,Math.log10(pressure/1000)*45-60)) : -60}deg)`;
  badge($("chamberState"),!connected ? "neutral" : plcState === 4 ? "fault" : plcState === 3 ? "passed" : plcState === 1 || plcState === 2 ? "running" : "neutral",connected ? stateNames[plcState] || `状态 ${plcState}` : "数据失联");
  $("plcAlarm").textContent = !good("alarm_code") ? "未知" : alarm === 0 ? "无报警" : alarm === 1 ? "01 · 阀门联锁" : alarm === 2 ? "02 · 非法命令" : `${alarm} · 报警`;
  $("plcAlarm").className = alarm && connected ? "text-bad" : "";
  $("controllerStatus").textContent = data.controller.message || "等待操作";
  $("controllerStatus").className = data.controller.fault ? "text-bad" : "";
  $("qualityStatus").textContent = !connected ? "未知" : !good("pressure_pa") ? (nodes["vacuum.pressure_pa"]?.status_code || "未知") : valid ? "Good" : "Good · 值异常";
  $("qualityStatus").className = !valid && connected ? "text-bad" : valid ? "text-good" : "";
  $("sampleAge").textContent = connected ? `最近采样 ${clock(snap.time)}` : "实时采样已中断";
  badge($("controlMode"),data.mode === "idle" ? data.needs_reset ? "error" : "neutral" : "running",data.mode === "idle" && data.needs_reset ? "需重置" : ({idle:"空闲可用",running:"pytest 独占",cleaning:"正在清理",resetting:"正在重置",starting:"正在启动",closed:"已关闭"})[data.mode] || data.mode);
  $("faultStatus").textContent = data.active_fault ? `当前条件：${faultNames[data.active_fault]}，重置后清除。` : "未设置手动注入条件";
  const run = data.run;
  if (run) {
    badge($("runStatus"),data.mode === "cleaning" ? "running" : run.status,data.mode === "cleaning" ? "正在清理" : undefined);
    const cases = Object.values(run.cases), passed = cases.filter(item => item.status === "passed").length;
    $("runCount").textContent = `${passed} / ${cases.length} 用例通过`;
    $("runTime").textContent = `${((run.ended || Date.now()/1000)-run.started).toFixed(1)} s`;
    $("reports").hidden = !run.ended;
  } else {
    badge($("runStatus"),"neutral","尚未运行");
    $("runCount").textContent="0 / 5 用例通过";
    $("runTime").textContent="—";
    $("reports").hidden=true;
  }
  for (const item of catalog) {
    const row = caseElements.get(item.id), result = run?.cases[item.id];
    row.card.classList.toggle("active",result?.status === "running");
    row.result.textContent = result ? labels[result.status] || result.status : "待运行";
    row.result.className = `case-result ${result?.status || "pending"}`;
    row.description.textContent = result?.status === "running" ? result.step : result?.duration ? `${result.duration.toFixed(1)} s · ${item.description}` : item.description;
  }
  updateButtons();
}

function appendEvents(events) {
  if (!events.length) return;
  const list = $("eventList");
  const atBottom = list.scrollHeight-list.scrollTop-list.clientHeight < 35;
  list.querySelector(".empty-events")?.remove();
  for (const event of events) {
    allEvents.push(event);
    if (event.kind === "injecting") markers.push(event);
    const row = document.createElement("div"); row.className = "event";
    const time = document.createElement("time"); time.textContent = clock(event.time);
    const kind = document.createElement("span"); kind.className = `event-kind ${event.kind}`; kind.textContent = phaseNames[event.kind] || "事件";
    const message = document.createElement("span"); message.className = "event-message";
    message.textContent = event.kind === "case_result" || event.kind === "run_result" ? `${event.message} · ${labels[event.status] || event.status}` : event.message;
    if (event.detail) row.title = event.detail;
    row.append(time,kind,message); list.append(row);
  }
  allEvents = allEvents.slice(-2000); markers = markers.slice(-100);
  while (list.children.length > 300) list.firstElementChild.remove();
  if (atBottom) list.scrollTop = list.scrollHeight;
  $("eventCount").textContent = `${allEvents.length} 条事件`;
}

function drawChart() {
  const canvas = $("pressureChart"), box = canvas.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
  if (!box.width || !box.height) return;
  canvas.width = Math.round(box.width*ratio); canvas.height = Math.round(box.height*ratio);
  const ctx = canvas.getContext("2d"); ctx.scale(ratio,ratio);
  const width = box.width, height = box.height, left=55, right=14, top=8, bottom=28;
  const w=width-left-right, h=height-top-bottom;
  ctx.font = '11px "Segoe UI", "Microsoft YaHei", sans-serif';
  ctx.lineWidth = 1;
  for (let value=0; value<=100000; value+=25000) {
    const y=top+h*(1-value/105000);
    ctx.strokeStyle="#edf1f7";ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();
    ctx.fillStyle="#95a2b5";ctx.textAlign="right";ctx.fillText(value === 0 ? "0" : `${value/1000}k`,left-9,y+4);
  }
  if (!samples.length) { ctx.textAlign="center";ctx.fillText("等待实时压力数据",width/2,height/2); return; }
  const end=samples[samples.length-1].time, start=Math.min(end-15,Math.max(samples[0].time,end-120));
  const x=time=>left+w*(time-start)/(end-start), y=value=>top+h*(1-Math.min(105000,Math.max(0,value))/105000);
  ctx.save();ctx.beginPath();ctx.rect(left,top,w,h+1);ctx.clip();
  ctx.strokeStyle="#2b70ef";ctx.lineWidth=2;ctx.beginPath();let segment=false;
  for (const sample of samples) {
    if (sample.time<start) continue;
    if (sample.pressure===null) {segment=false;continue;}
    if (segment) ctx.lineTo(x(sample.time),y(sample.pressure)); else ctx.moveTo(x(sample.time),y(sample.pressure));
    segment=true;
  }
  ctx.stroke();
  ctx.strokeStyle="#df8290";ctx.lineWidth=1;ctx.setLineDash([3,3]);
  for (const marker of markers) if (marker.time>=start && marker.time<=end) {ctx.beginPath();ctx.moveTo(x(marker.time),top);ctx.lineTo(x(marker.time),top+h);ctx.stroke();}
  ctx.restore();ctx.fillStyle="#95a2b5";
  for (let i=0;i<4;i++) {const time=start+(end-start)*i/3;ctx.textAlign=i===0?"left":i===3?"right":"center";ctx.fillText(clock(time),x(time),height-6);}
}

async function fetchState() {
  try {
    let data = await api(`/api/state?after=${eventSeq}&sample_after=${sampleSeq}`);
    if (instanceId && data.instance_id !== instanceId) {
      eventSeq=0;sampleSeq=0;samples=[];markers=[];allEvents=[];
      $("eventList").replaceChildren();$("eventCount").textContent="0 条事件";
      data=await api("/api/state");
    }
    instanceId=data.instance_id;
    eventSeq=data.event_seq;sampleSeq=data.sample_seq;
    samples.push(...data.samples);samples=samples.slice(-1200);
    appendEvents(data.events);renderState(data);drawChart();
  } catch (error) {
    connected=false;$("connectionText").textContent="本地服务连接中断";$("connectionLed").className="led bad";
    if (currentState) renderState({...currentState,snapshot:{connected:false,nodes:{}}});
    updateButtons();
  }
}

function pollOnce() {
  if (!pollPromise) pollPromise = fetchState().finally(() => {pollPromise=null;});
  return pollPromise;
}

function registerAgentTools() {
  if (!document.modelContext?.registerTool) return;
  const lifecycle=new AbortController();
  window.addEventListener("pagehide",()=>lifecycle.abort(),{once:true});
  const empty={type:"object",properties:{},additionalProperties:false};
  const tools=[
    {name:"get_demo_state",description:"读取本机 PLC 仿真状态与本轮 pytest 结果。",inputSchema:empty,annotations:{readOnlyHint:true},execute:async()=>{await pollOnce();return {mode:currentState?.mode,snapshot:currentState?.snapshot,run:currentState?.run};}},
    {name:"run_demo_cases",description:"实际运行指定内置 pytest 用例，执行期间独占 PLC 控制。",inputSchema:{type:"object",properties:{case_ids:{type:"array",items:{type:"string",enum:catalog.map(c=>c.id)},minItems:1,uniqueItems:true}},required:["case_ids"],additionalProperties:false},execute:input=>action("/api/runs",input)},
    {name:"cancel_demo_run",description:"请求停止当前 pytest 运行并等待环境清理。",inputSchema:empty,execute:()=>action("/api/runs/cancel",{})},
    {name:"reset_demo_environment",description:"空闲时停止手动控制器和故障注入，恢复 PLC 节点基线。",inputSchema:empty,execute:()=>action("/api/reset",{})}
  ];
  for (const tool of tools) {
    try {Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});} catch (_) { /* Optional browser capability. */ }
  }
}

async function initialize() {
  updateButtons();
  try {catalog=(await api("/api/cases")).cases;buildCases();} catch(error) {showNotice("无法读取演示用例："+error.message);return;}
  $("runAll").onclick=()=>action("/api/runs",{case_ids:catalog.map(c=>c.id)}).catch(()=>{});
  $("runSelected").onclick=()=>action("/api/runs",{case_ids:catalog.filter(c=>selected.has(c.id)).map(c=>c.id)}).catch(()=>{});
  $("cancelRun").onclick=()=>action("/api/runs/cancel",{}).catch(()=>{});
  $("resetEnvironment").onclick=()=>action("/api/reset",{}).catch(()=>{});
  $("injectFault").onclick=()=>action("/api/manual",{action:"inject",fault:$("faultSelect").value}).catch(()=>{});
  document.querySelectorAll("[data-action]").forEach(button=>button.onclick=()=>action("/api/manual",{action:button.dataset.action}).catch(()=>{}));
  new ResizeObserver(drawChart).observe($("pressureChart").parentElement);
  registerAgentTools();
  async function loop() {await pollOnce();setTimeout(loop,200);}
  loop();
}
initialize();
