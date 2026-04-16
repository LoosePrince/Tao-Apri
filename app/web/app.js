const API_RUNTIME_CONFIG = "/admin/runtime-config";
const API_VALIDATE = "/admin/runtime-config/validate";
const API_APPLY = "/admin/runtime-config/apply";
const API_EXPORT = "/admin/runtime-config/export";
const API_STATUS = "/admin/runtime-status";

const GROUP_ORDER = ["app", "emotion", "retrieval", "persona", "profile", "llm", "rhythm", "jobs", "onebot", "storage"];
const GROUP_LABEL = {
  app: "应用",
  storage: "存储（只读）",
  emotion: "情绪",
  retrieval: "检索",
  persona: "人设",
  profile: "画像",
  llm: "LLM",
  rhythm: "节奏",
  jobs: "任务",
  onebot: "OneBot",
};

function el(query) {
  return document.querySelector(query);
}

function setStatus(msg, isErr) {
  const node = document.getElementById("status-bar");
  node.textContent = msg || "—";
  node.style.color = isErr ? "var(--danger)" : "var(--text-muted)";
}

let toastTid;
function toast(msg, isErr) {
  const node = document.getElementById("toast");
  node.textContent = msg;
  node.className = "show" + (isErr ? " err" : "");
  clearTimeout(toastTid);
  toastTid = setTimeout(() => {
    node.className = "";
  }, 3500);
}

let _authInProgress = false;
let _loadConfigRetriedOnce = false;

function showAuthUI(allowedUin) {
  const authRoot = el("#auth-root");
  authRoot.style.display = "flex";
  authRoot.style.justifyContent = "center";
  const formRoot = el("#form-root");
  formRoot.style.display = "none";
  if (allowedUin !== undefined) {
    el("#allowed-uin").textContent = allowedUin;
  }
}

function hideAuthUI() {
  el("#auth-root").style.display = "none";
  el("#form-root").style.display = "block";
}

function setAuthMsg(msg, isErr) {
  const node = el("#auth-msg");
  node.textContent = msg || "";
  node.style.color = isErr ? "var(--danger)" : "var(--text-muted)";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function startQrLoginFlow() {
  if (_authInProgress) return false;
  _authInProgress = true;
  try {
    showAuthUI();

    while (true) {
      setAuthMsg("正在获取登录二维码…");
      setStatus("获取 QQ 登录二维码…");

      const startResp = await fetch("/admin/auth/qq/start", { credentials: "include" });
      const startData = await startResp.json().catch(() => ({}));
      if (!startResp.ok || !startData.ok) {
        const msg = startData.msg || `HTTP ${startResp.status}`;
        setStatus("获取二维码失败：" + msg, true);
        setAuthMsg("获取二维码失败：" + msg, true);
        await sleep(2500);
        continue;
      }

      const code = startData.code || "";
      const qrUrl = startData.qrUrl || "";
      const allowedUin = startData.allowedUin || "";

      showAuthUI(allowedUin);
      renderQrCode(qrUrl);

      setAuthMsg("请使用 QQ 扫码登录（允许账号匹配： " + allowedUin + " ）");
      setStatus("请扫码登录…");

      // 轮询状态：最多约 2 分钟
      for (let i = 0; i < 80; i++) {
        await sleep(1500);
        const statusResp = await fetch("/admin/auth/qq/status?code=" + encodeURIComponent(code), {
          credentials: "include",
        });
        const statusData = await statusResp.json().catch(() => ({}));

        if (statusData.state === "ok") {
          setAuthMsg("登录成功，正在加载…");
          toast("登录成功");
          hideAuthUI();
          return true;
        }

        if (statusData.state === "used") {
          setAuthMsg("二维码已失效，请点击“换一张”。");
          break;
        }

        if (statusData.state === "error") {
          const msg = statusData.msg || "登录失败";
          setAuthMsg(msg, true);
          setStatus(msg, true);
          break;
        }
      }
      // 超时或失效：重新拉取二维码
      setStatus("二维码已过期，正在换新…");
    }
  } finally {
    _authInProgress = false;
  }
}

function renderQrCode(qrText) {
  const container = el("#qr-container");
  if (!container) return;
  container.innerHTML = "";

  if (typeof window.QRCode === "undefined") {
    setAuthMsg("缺少 QRCode 库，无法渲染二维码。", true);
    return;
  }

  // qrcodejs: new QRCode(containerEl, { text, width, height, correctLevel })
  // 这里把后端返回的 qrUrl 当作二维码内容进行编码。
  // 即使 qrUrl 本身不是图片链接，也能确保页面生成“真正可扫码二维码”。
  new window.QRCode(container, {
    text: String(qrText || ""),
    width: 200,
    height: 200,
  });
}

function escapeText(s) {
  // We only use it for attributes/values; keep simple.
  return String(s ?? "");
}

function deepSet(obj, pathArr, value) {
  let cur = obj;
  for (let i = 0; i < pathArr.length - 1; i++) {
    const k = pathArr[i];
    if (cur[k] === undefined || cur[k] === null || typeof cur[k] !== "object") cur[k] = {};
    cur = cur[k];
  }
  cur[pathArr[pathArr.length - 1]] = value;
}

function groupByFieldPath(fields) {
  const groups = {};
  for (const f of fields) {
    const top = f.path.split(".")[0];
    groups[top] = groups[top] || [];
    groups[top].push(f);
  }
  for (const k of Object.keys(groups)) {
    groups[k].sort((a, b) => a.path.localeCompare(b.path));
  }
  return groups;
}

let _lastRuntime = null;

function buildInputForField(field) {
  const path = field.path;
  const type = field.type;
  const disabled = !field.editable;
  const value = field.value;

  const wrap = document.createElement("div");
  wrap.className = "field" + (type === "array" ? " full" : "");

  const flabel = document.createElement("div");
  flabel.className = "flabel";
  flabel.textContent = field.label;
  wrap.appendChild(flabel);

  const descText = (field.desc || "").trim();
  if (descText) {
    const fdesc = document.createElement("div");
    fdesc.className = "fdesc";
    fdesc.textContent = descText;
    wrap.appendChild(fdesc);
  }

  if (type === "bool") {
    const tw = document.createElement("div");
    tw.className = "twrap";

    const label = document.createElement("label");
    label.className = "toggle";
    const checked = !!value;
    label.innerHTML = `
      <input type="checkbox" data-path="${path}" data-type="bool" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
      <span class="slider"></span>
    `;
    tw.appendChild(label);

    const tlabel = document.createElement("span");
    tlabel.className = "tlabel";
    tlabel.textContent = checked ? "开启" : "关闭";
    if (!disabled) {
      label.querySelector("input").addEventListener("change", (e) => {
        tlabel.textContent = e.target.checked ? "开启" : "关闭";
      });
    }
    tw.appendChild(tlabel);
    wrap.appendChild(tw);
    return wrap;
  }

  if (type === "number") {
    const inp = document.createElement("input");
    inp.type = "number";
    inp.dataset.path = path;
    inp.dataset.type = "number";
    inp.value = value === null || value === undefined ? "" : String(value);
    inp.disabled = disabled;
    wrap.appendChild(inp);
    return wrap;
  }

  if (type === "password") {
    const inp = document.createElement("input");
    inp.type = "password";
    inp.dataset.path = path;
    inp.dataset.type = "password";
    inp.value = "";
    inp.placeholder = "留空保留原值";
    inp.autocomplete = "off";
    inp.disabled = disabled;
    wrap.appendChild(inp);
    return wrap;
  }

  if (type === "array") {
    const ta = document.createElement("textarea");
    ta.rows = 3;
    ta.spellcheck = false;
    ta.dataset.path = path;
    ta.dataset.type = "array";
    const arr = Array.isArray(value) ? value : [];
    ta.value = arr.join("\n");
    ta.disabled = disabled;
    wrap.appendChild(ta);
    return wrap;
  }

  // text fallback
  const inp = document.createElement("input");
  inp.type = "text";
  inp.dataset.path = path;
  inp.dataset.type = "text";
  inp.value = value === null || value === undefined ? "" : escapeText(value);
  inp.disabled = disabled;
  wrap.appendChild(inp);
  return wrap;
}

function buildForm(runtime) {
  const root = document.getElementById("form-root");
  root.innerHTML = "";
  _lastRuntime = runtime;

  const config = runtime.config || {};
  const fields = runtime.fields || [];
  const groups = groupByFieldPath(fields);

  let openFirst = true;

  for (const topKey of GROUP_ORDER) {
    if (!groups[topKey] || groups[topKey].length === 0) continue;

    const det = document.createElement("details");
    if (openFirst) det.open = true;
    det.className = topKey === "storage" ? "sub" : "";
    openFirst = false;

    const summary = document.createElement("summary");
    summary.textContent = GROUP_LABEL[topKey] || topKey;
    det.appendChild(summary);

    const body = document.createElement("div");
    body.className = "sec-body";

    const grid = document.createElement("div");
    grid.className = "fields";
    for (const field of groups[topKey]) {
      grid.appendChild(buildInputForField(field));
    }

    body.appendChild(grid);
    det.appendChild(body);
    root.appendChild(det);
  }
}

function collectUpdates() {
  const updates = {};
  const inputs = document.querySelectorAll("[data-path][data-type]");
  inputs.forEach((node) => {
    if (node.disabled) return;
    const path = node.dataset.path;
    const type = node.dataset.type;
    const pathArr = path.split(".");

    let value;
    if (type === "bool") {
      value = !!node.checked;
    } else if (type === "number") {
      const n = parseFloat(node.value);
      value = Number.isNaN(n) ? 0 : n;
    } else if (type === "password") {
      // empty => keep original => omit
      if (!node.value) return;
      value = node.value;
    } else if (type === "array") {
      value = node.value
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
    } else {
      value = node.value;
    }

    deepSet(updates, pathArr, value);
  });

  return updates;
}

async function loadConfig() {
  setStatus("正在加载配置…");
  document.getElementById("btn-load").disabled = true;
  try {
    const r = await fetch(API_RUNTIME_CONFIG, { credentials: "include" });
    if (r.status === 403) {
      if (!_loadConfigRetriedOnce) {
        _loadConfigRetriedOnce = true;
        await startQrLoginFlow();
        return loadConfig();
      }
      setStatus("未获得管理权限，请完成 QQ 扫码登录。", true);
      return;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    buildForm(d);
    setStatus("配置加载成功");
    toast("配置加载成功");
  } catch (e) {
    setStatus("加载失败：" + e.message, true);
    toast("加载失败：" + e.message, true);
  } finally {
    document.getElementById("btn-load").disabled = false;
  }
}

async function validateConfig() {
  const updates = collectUpdates();
  setStatus("正在校验…");
  document.getElementById("btn-validate").disabled = true;
  try {
    const r = await fetch(API_VALIDATE, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    if (r.status === 403) {
      await startQrLoginFlow();
      return;
    }
    const d = await r.json();
    if (!d.ok) {
      const msg = (d.errors || []).join(", ") || "校验失败";
      setStatus(msg, true);
      toast("校验失败", true);
      return;
    }
    setStatus("校验通过");
    toast("校验通过");
  } catch (e) {
    setStatus("校验失败：" + e.message, true);
    toast("校验失败：" + e.message, true);
  } finally {
    document.getElementById("btn-validate").disabled = false;
  }
}

async function applyConfig() {
  const updates = collectUpdates();
  setStatus("正在应用…");
  document.getElementById("btn-apply").disabled = true;
  try {
    const r = await fetch(API_APPLY, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    if (r.status === 403) {
      await startQrLoginFlow();
      return;
    }
    const d = await r.json();
    if (!d.ok) {
      const msg = (d.errors || []).join(", ") || "应用失败";
      setStatus(msg, true);
      toast("应用失败", true);
      return;
    }
    setStatus("应用成功");
    toast("应用成功");
    await loadConfig(); // refresh values (especially for password-masked fields)
  } catch (e) {
    setStatus("应用失败：" + e.message, true);
    toast("应用失败：" + e.message, true);
  } finally {
    document.getElementById("btn-apply").disabled = false;
  }
}

async function exportEnv() {
  setStatus("正在导出 .env…");
  document.getElementById("btn-export").disabled = true;
  try {
    const r = await fetch(API_EXPORT, { method: "POST", credentials: "include" });
    if (r.status === 403) {
      await startQrLoginFlow();
      return;
    }
    const d = await r.json();
    if (!d.ok) throw new Error(d.message || "export failed");
    const content = d.content || "";
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "runtime.env";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setStatus("导出完成");
    toast("导出完成");
  } catch (e) {
    setStatus("导出失败：" + e.message, true);
    toast("导出失败：" + e.message, true);
  } finally {
    document.getElementById("btn-export").disabled = false;
  }
}

async function fetchStatus() {
  try {
    const r = await fetch(API_STATUS, { credentials: "include" });
    if (r.status === 403) return;
    if (!r.ok) return;
    const d = await r.json();
    el("#info-row").style.display = "flex";
    el("#bv").textContent = "models=" + (d?.llm?.models?.length ?? 0);
    el("#bc").textContent = "onebot=" + (d?.onebot?.enabled ? "on" : "off");
  } catch (e) {
    // optional
  }
}

async function init() {
  fetchStatus();
  await loadConfig();
  document.getElementById("btn-load").addEventListener("click", loadConfig);
  document.getElementById("btn-validate").addEventListener("click", validateConfig);
  document.getElementById("btn-apply").addEventListener("click", applyConfig);
  document.getElementById("btn-export").addEventListener("click", exportEnv);

  const refreshBtn = document.getElementById("btn-refresh-qr");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      // Force re-run login flow (new code).
      _loadConfigRetriedOnce = false;
      startQrLoginFlow();
    });
  }
}

init();

