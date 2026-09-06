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
let currentKind = "plc", globalActive = null, switching = false;
const views = new Map();
const titles = {plc:"真空腔室",sdk:"旋转轴",tcp:"探测器"};
const plcFaults = $("faultSelect").innerHTML;
const plcLegend = $("chartLegend").innerHTML;

function showNotice(message) { $("notice").textContent = message; $("notice").hidden = !message; }
function badge(element, status, label) { element.className = `badge ${status}`; element.textContent = label || labels[status] || status; }
function number(value, digits=0) { return Number(value).toLocaleString("zh-CN",{maximumFractionDigits:digits}); }
function clock(time) { return new Date(time*1000).toLocaleTimeString("zh-CN",{hour12:false}); }

async function api(path, data) {
  if (path.startsWith("/api/") && !path.startsWith("/api/demos")) path = `/api/demos/${currentKind}/` + path.slice(5);
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
  list.replaceChildren(); caseElements.clear();
  for (const item of catalog) {
    if (!views.has(currentKind)) selected.add(item.id);
    const card = document.createElement("div"); card.className = "case-card";
    const row = document.createElement("div"); row.className = "case-title-row";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = selected.has(item.id); checkbox.id = `select-${item.id}`;
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
  $("runAll").disabled = !idle || active || !!globalActive || switching;
  $("runSelected").disabled = !idle || active || !!globalActive || !selected.size || switching;
  $("cancelRun").disabled = currentState?.mode !== "running";
  $("resetEnvironment").disabled = !currentState || currentState.mode !== "idle";
  $("faultSelect").disabled = !idle || !!currentState?.active_fault;
  $("injectFault").disabled = !idle || !!currentState?.active_fault;
  document.querySelectorAll("[data-action]").forEach(button => {
    button.disabled = !idle || switching || (currentKind === "plc"
      ? (["pump","vent"].includes(button.dataset.action) && (active || !!currentState?.active_fault))
      : active && button.dataset.action !== "stop");
  });
  for (const item of caseElements.values()) item.checkbox.disabled = !idle;
}

function renderState(data) {
  currentState = data;
  const snap = data.snapshot;
  connected = snap.connected && Date.now()/1000 - snap.time < 3;
  $("connectionLed").className = `led ${connected ? "good" : "bad"}`;
  $("connectionText").textContent = connected ? data.needs_reset ? "连接已恢复 · 请重置环境" : "仿真环境已连接" : "设备连接中断";
  if (data.needs_reset) $("connectionLed").className="led bad";
  $("endpoint").textContent = Array.isArray(data.endpoint) ? data.endpoint.join(":") : data.endpoint || "本地独享仿真环境";
  if (currentKind === "plc") {
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
  } else renderProtocol(data, connected);
  $("sampleAge").textContent = connected ? `最近采样 ${clock(snap.time)}` : "实时采样已中断";
  badge($("controlMode"),data.mode === "idle" ? data.needs_reset ? "error" : "neutral" : "running",data.mode === "idle" && data.needs_reset ? "需重置" : ({idle:"空闲可用",running:"pytest 独占",cleaning:"正在清理",resetting:"正在重置",starting:"正在启动",closed:"已关闭"})[data.mode] || data.mode);
  $("faultStatus").textContent = data.active_fault ? `当前条件：${(currentKind === "plc" ? faultNames[data.active_fault] : catalog.find(c=>c.id===data.active_fault)?.title || data.active_fault)}，重置后清除。` : "未设置手动注入条件";
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
  if (currentKind !== "plc") { drawProtocolChart(); return; }
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
  if (switching) return;
  const kind = currentKind;
  try {
    const summary = await api("/api/demos");
    if (kind !== currentKind || switching) return;
    globalActive = summary.active_run;
    $("globalRun").textContent = globalActive ? `${globalActive.toUpperCase()} 自动用例运行中 · 可切换查看` : "三个协议 · 独立环境";
    for (const demo of summary.demos) {
      document.querySelector(`[data-demo="${demo.id}"] small`).textContent =
        demo.mode === "running" || demo.mode === "cleaning" ? "运行中" : demo.status === "starting" ? "启动中" : demo.status === "error" ? "启动失败" : "";
    }
    const availability = summary.demos.find(d=>d.id===kind);
    $("initialization").hidden = availability.status === "ready";
    $("retryDemo").hidden = availability.status !== "error";
    if (availability.status !== "ready") {
      $("initMessage").textContent = availability.error || `正在启动 ${kind.toUpperCase()} 演示${kind === "sdk" ? "，首次启动会构建原生 SDK" : ""}…`;
      connected = false; updateButtons(); return;
    }
    let data = await api(`/api/demos/${kind}/state?after=${eventSeq}&sample_after=${sampleSeq}`);
    if (kind !== currentKind || switching) return;
    if (instanceId && data.instance_id !== instanceId) {
      eventSeq=0;sampleSeq=0;samples=[];markers=[];allEvents=[];
      $("eventList").replaceChildren();$("eventCount").textContent="0 条事件";
      data=await api(`/api/demos/${kind}/state`);
      if (kind !== currentKind || switching) return;
    }
    instanceId=data.instance_id;
    eventSeq=data.event_seq;sampleSeq=data.sample_seq;
    samples.push(...data.samples);samples=samples.slice(-1200);
    appendEvents(data.events);renderState(data);drawChart();
  } catch (error) {
    if (kind !== currentKind || switching) return;
    connected=false;$("connectionText").textContent="本地服务连接中断";$("connectionLed").className="led bad";
    if (currentState) renderState({...currentState,snapshot:{connected:false,nodes:{},values:{}}});
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
    {name:"get_demo_state",description:"读取当前协议仿真状态与本轮 pytest 结果。",inputSchema:empty,annotations:{readOnlyHint:true},execute:async()=>{await pollOnce();return {mode:currentState?.mode,snapshot:currentState?.snapshot,run:currentState?.run};}},
    {name:"run_demo_cases",description:"实际运行指定内置 pytest 用例，执行期间独占当前设备控制。",inputSchema:{type:"object",properties:{case_ids:{type:"array",items:{type:"string",enum:["normal","interlock","sensor","pressure","timeout","sdk_error","query_error","limit","recover","device_error","measurement_error"]},minItems:1,uniqueItems:true}},required:["case_ids"],additionalProperties:false},execute:input=>action("/api/runs",input)},
    {name:"cancel_demo_run",description:"请求停止当前 pytest 运行并等待环境清理。",inputSchema:empty,execute:()=>action("/api/runs/cancel",{})},
    {name:"reset_demo_environment",description:"空闲时停止手动控制器和故障注入，恢复当前设备基线。",inputSchema:empty,execute:()=>action("/api/reset",{})}
  ];
  for (const tool of tools) {
    try {Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});} catch (_) { /* Optional browser capability. */ }
  }
}

async function initialize() {
  updateButtons();
  try {catalog=(await api("/api/cases")).cases;buildCases();} catch(error) {showNotice("无法读取演示用例："+error.message);return;}
  document.querySelectorAll("[data-demo]").forEach(button=>button.onclick=()=>switchDemo(button.dataset.demo));
  $("retryDemo").onclick=()=>action(`/api/demos/${currentKind}/initialize`,{}).catch(()=>{});
  $("runAll").onclick=()=>action("/api/runs",{case_ids:catalog.map(c=>c.id)}).catch(()=>{});
  $("runSelected").onclick=()=>action("/api/runs",{case_ids:catalog.filter(c=>selected.has(c.id)).map(c=>c.id)}).catch(()=>{});
  $("cancelRun").onclick=()=>action("/api/runs/cancel",{}).catch(()=>{});
  $("resetEnvironment").onclick=()=>action("/api/reset",{}).catch(()=>{});
  $("injectFault").onclick=()=>action("/api/manual",{action:"inject",fault:$("faultSelect").value}).catch(()=>{});
  document.querySelectorAll("[data-action]").forEach(button=>button.onclick=()=>action("/api/manual",{action:button.dataset.action,...(currentKind === "sdk" ? {target:Number($("targetInput").value),speed:Number($("speedInput").value)} : {})}).catch(()=>{}));
  new ResizeObserver(drawChart).observe($("pressureChart").parentElement);
  registerAgentTools();
  async function loop() {await pollOnce();setTimeout(loop,200);}
  loop();
}
initialize();

async function switchDemo(kind) {
  if (switching || kind === currentKind) return;
  switching = true;
  try {
    if (pollPromise) await pollPromise;
    views.set(currentKind, {currentState,eventSeq,sampleSeq,samples,markers,allEvents,instanceId,selected:[...selected],
                           target:$("targetInput").value,speed:$("speedInput").value});
    currentKind = kind;
    const saved = views.get(kind);
    currentState = saved?.currentState || null;
    eventSeq=saved?.eventSeq || 0;sampleSeq=saved?.sampleSeq || 0;samples=saved?.samples || [];
    markers=[];allEvents=[];instanceId=saved?.instanceId || null;
    selected.clear();(saved?.selected || []).forEach(id=>selected.add(id));
    connected = false; showNotice("");
    $("eventList").replaceChildren();$("eventCount").textContent="0 条事件";
    $("reports").hidden = true;
    $("initialization").hidden = true;
    $("trafficList").replaceChildren();
    $("faultSelect").innerHTML = kind === "plc" ? plcFaults : "";
    $("sceneTitle").textContent = titles[kind];
    $("demoEyebrow").textContent = {plc:"PLC / VACUUM CHAMBER 01",sdk:"NATIVE SDK / ROTARY AXIS 01",tcp:"MODBUS TCP / DETECTOR 01"}[kind];
    $("schematic").hidden = kind !== "plc";
    $("protocolScene").hidden = kind === "plc";
    $("axisDiagram").toggleAttribute("hidden",kind !== "sdk");$("detectorDiagram").toggleAttribute("hidden",kind !== "tcp");
    $("plcControls").hidden = kind !== "plc";$("sdkControls").hidden = kind !== "sdk";$("tcpControls").hidden = kind !== "tcp";
    $("injectionHint").hidden = kind === "plc";$("trafficPanel").hidden = kind === "plc";
    $("alarmLabel").textContent = kind === "plc" ? "PLC 报警" : kind === "sdk" ? "轴报警" : "设备状态";
    $("qualityLabel").textContent = kind === "plc" ? "数据质量" : "反馈来源";
    $("sceneNote").textContent = kind === "plc" ? "流向由实际状态驱动" : kind === "sdk" ? "蓝色：实际位置 · 橙色：目标" : "显示 SUT 已收到的反馈，不额外发送查询";
    $("trendTitle").textContent = kind === "plc" ? "压力趋势" : kind === "sdk" ? "运动趋势" : "测量趋势";
    $("chartLegend").innerHTML = kind === "plc" ? plcLegend : kind === "sdk" ? '<span style="color:#2765e9">位置 / °</span><span style="color:#eaa24a">目标 / °</span><span style="color:#279883">速度 / °/s</span>' : '<span>测量值</span>';
    $("pressureChart").setAttribute("aria-label",kind === "plc" ? "实际压力曲线" : kind === "sdk" ? "实际位置、目标和速度曲线" : "SUT 同步测量值曲线");
    document.querySelectorAll("[data-demo]").forEach(button=>{
      button.classList.toggle("active",button.dataset.demo===kind);
      button.setAttribute("aria-pressed",String(button.dataset.demo===kind));
    });
    document.querySelectorAll("#reports a").forEach(link=>link.href=`/api/demos/${kind}/artifacts/${link.href.split("/").pop()}`);
    if (saved) {$("targetInput").value=saved.target;$("speedInput").value=saved.speed;}
    catalog=(await api(`/api/demos/${kind}/cases`)).cases;buildCases();
    if (kind !== "plc") for (const row of catalog.filter(c=>c.id!=="normal")) {
      const option=document.createElement("option");option.value=row.id;option.textContent=row.title;$("faultSelect").append(option);
    }
    appendEvents(saved?.allEvents || []);markers=saved?.markers || [];
    renderState(currentState || {mode:"starting",snapshot:{connected:false,nodes:{},values:{}},controller:{},run:null});
    drawChart();
    await api(`/api/demos/${kind}/initialize`,{});
  } catch(error) {showNotice(error.message);}
  finally {switching=false;await pollOnce();}
}

function renderProtocol(data, live) {
  const values = live ? data.snapshot.values || {} : {};
  const val = (key, digits=1) => typeof values[key] === "number" ? number(values[key],digits) : "—";
  if (currentKind === "sdk") {
    const position = typeof values.position === "number" ? values.position : 0;
    $("axisNeedle").setAttribute("transform",`rotate(${position} 235 135)`);
    $("axisNeedle").style.opacity = live && typeof values.position === "number" ? "1" : "0";
    $("axisTarget").setAttribute("transform",`rotate(${values.target || 0} 235 135)`);
    $("axisTarget").style.opacity = live ? "1" : "0";
    $("axisPosition").textContent = val("position")+"°";
    $("axisTargetText").textContent = `目标 ${val("target")}°`;
    $("axisVelocity").textContent = `速度 ${val("velocity")}°/s`;
    $("axisFlags").textContent = !live ? "数据已失效" : `${values.enabled ? "已使能" : "未使能"} · ${values.homed ? "已回零" : "未回零"}`;
    $("deviceFlags").textContent = live ? `忙碌 ${values.busy ? "是" : "否"} · 完成 ${values.done ? "是" : "否"} · 阶段 ${values.phase || "—"} · 故障 ${Object.entries(values.faults || {}).filter(([,on])=>on).map(([key])=>key).join(" / ") || "无"}` : "设备反馈不可用";
    $("plcAlarm").textContent = !live ? "未知" : values.alarm ? `${values.alarm} · 报警` : "无报警";
    badge($("chamberState"),!live ? "neutral" : values.alarm ? "fault" : values.busy ? "running" : "neutral",!live ? "等待数据" : values.alarm ? "轴报警" : values.busy ? "运动中" : "静止");
  } else {
    $("detectorReady").textContent = values.ready === undefined ? "等待业务读取" : values.ready ? "设备已就绪" : "设备未就绪";
    $("detectorLed").setAttribute("fill",values.ready === undefined ? "#a8b3c5" : values.ready ? "#279883" : "#eaa24a");
    $("detectorMeasurement").textContent = val("measurement",0);
    $("detectorCode").textContent = `设备状态 ${val("status",0)}`;
    $("deviceFlags").textContent = live ? "状态与测量来自当前业务操作的实际反馈" : "设备反馈不可用";
    $("plcAlarm").textContent = values.status === undefined ? "尚未读取" : String(values.status);
    badge($("chamberState"),values.status ? "fault" : values.ready ? "passed" : "neutral",values.status ? "设备异常" : values.ready ? "已就绪" : "等待就绪");
  }
  $("controllerStatus").textContent=data.controller.message || "等待操作";
  $("controllerStatus").className=data.controller.fault ? "text-bad" : "";
  $("qualityStatus").textContent=live ? currentKind === "sdk" ? "实时设备状态" : "SUT 已同步反馈" : "未知";
  $("qualityStatus").className=live ? "text-good" : "";
  $("plcAlarm").className = (values.alarm || values.status) ? "text-bad" : "";
  const entries=Object.entries(live ? data.snapshot.sequences || {} : {}).filter(([,v])=>v.values.length);
  $("sequenceStatus").textContent=entries.length ? entries.map(([key,v])=>`${key}：已用 ${v.cursor} / ${v.values.length}`).join(" · ") : "无预置响应序列";
  const list=$("trafficList");list.replaceChildren();
  for (const entry of (live ? data.snapshot.traffic || [] : []).slice(-25).reverse()) {
    const row=document.createElement("div");row.className="traffic-row";
    row.title=JSON.stringify(entry);
    row.textContent=currentKind === "sdk" ? entry.event === "fault" ? `故障 ${entry.name}：${entry.active ? "启用" : "清除"}`
      : `${entry.target}(${Object.entries(entry.args || {}).map(([k,v])=>`${k}=${v}`).join(", ")}) → 返回 ${entry.return_value}${entry.overridden ? " · 注入响应" : ""}`
      : `${entry.target || "协议错误"} · 请求 #${entry.request_id ?? "—"} → ${JSON.stringify(entry.fields || entry.error)}${entry.overridden ? " · 注入响应" : ""}`;
    list.append(row);
  }
}

function drawProtocolChart() {
  const canvas=$("pressureChart"),box=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio || 1;
  if (!box.width || !box.height) return;
  canvas.width=box.width*ratio;canvas.height=box.height*ratio;
  const ctx=canvas.getContext("2d");ctx.scale(ratio,ratio);
  const left=48,top=10,w=box.width-64,h=box.height-40;
  const keys=currentKind === "sdk" ? ["position","target","velocity"] : ["measurement"];
  const numbers=samples.flatMap(s=>keys.map(k=>s[k])).filter(v=>typeof v === "number" && Number.isFinite(v));
  const minimum=Math.min(0,...numbers), maximum=Math.max(currentKind === "sdk" ? 90 : 120,...numbers), range=maximum-minimum || 1;
  const end=samples.at(-1)?.time || Date.now()/1000,start=Math.min(end-15,Math.max(samples[0]?.time || end,end-120));
  const x=t=>left+w*(t-start)/(end-start), y=v=>top+h*(1-(v-minimum)/range);
  ctx.font='11px "Segoe UI",sans-serif';ctx.lineWidth=1;
  for(let i=0;i<=4;i++) {const v=minimum+range*i/4;ctx.strokeStyle="#edf1f7";ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(left+w,y(v));ctx.stroke();ctx.fillStyle="#95a2b5";ctx.textAlign="right";ctx.fillText(number(v,1),left-8,y(v)+4);}
  ctx.save();ctx.beginPath();ctx.rect(left,top,w,h);ctx.clip();
  keys.forEach((key,index)=>{ctx.strokeStyle=["#2765e9","#eaa24a","#279883"][index];ctx.lineWidth=2;ctx.beginPath();let segment=false;
    for(const sample of samples) {if(sample.time<start)continue;const value=sample[key];if(typeof value!=="number" || !Number.isFinite(value)){segment=false;continue;}if(segment)ctx.lineTo(x(sample.time),y(value));else ctx.moveTo(x(sample.time),y(value));segment=true;}ctx.stroke();});
  ctx.restore();ctx.fillStyle="#95a2b5";
  for(let i=0;i<4;i++){const t=start+(end-start)*i/3;ctx.textAlign=i===0?"left":i===3?"right":"center";ctx.fillText(clock(t),x(t),box.height-6);}
}
