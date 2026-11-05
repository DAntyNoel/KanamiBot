"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const input = $("#file-input");
const dropzone = $("#dropzone");
const records = [];
let config = null;
let processing = false;
let counter = 0;
let dragDepth = 0;
let selectionInitialized = false;
let modelBusy = null;
let preferredModels = new Set();
const modelToggles = Array.from(document.querySelectorAll("input[data-model]"));
const modelButtons = Array.from(
  document.querySelectorAll("[data-model-action]"),
);
const selectionKey = "kanami-image-lab-models-v1";
const selectedModels = () =>
  modelToggles
    .filter((toggle) => toggle.checked && !toggle.disabled)
    .map((toggle) => toggle.dataset.model);

function initializeSelection() {
  if (!selectionInitialized) {
    preferredModels = new Set(
      modelToggles.map((toggle) => toggle.dataset.model),
    );
    try {
      const value = JSON.parse(localStorage.getItem(selectionKey));
      if (Array.isArray(value)) preferredModels = new Set(value);
    } catch (_) {
      /* Browser storage can be unavailable. */
    }
    selectionInitialized = true;
  }
  for (const toggle of modelToggles) {
    const key = toggle.dataset.model;
    const state = config.model_states[key];
    toggle.disabled = !state.loaded || Boolean(modelBusy);
    toggle.checked = state.loaded && preferredModels.has(key);
    const item = toggle.closest(".model-item");
    item.classList.toggle("unavailable", !state.enabled);
    item.classList.toggle("unloaded", state.enabled && !state.loaded);
    $(".model-load-state", item).textContent = !state.enabled
      ? "后台已禁用"
      : state.loaded
        ? "已加载"
        : "已卸载";
  }
  updateModelButtons();
}

function updateModelButtons() {
  for (const button of modelButtons) {
    const key = button.dataset.modelAction;
    const state = config?.model_states[key];
    button.disabled = !state?.enabled || processing || Boolean(modelBusy);
    button.textContent =
      modelBusy?.key === key
        ? modelBusy.action === "unload"
          ? "卸载中…"
          : "加载中…"
        : state?.loaded
          ? "卸载"
          : "加载";
    button.setAttribute("aria-label", `${button.textContent} ${key}`);
  }
}

for (const toggle of modelToggles)
  toggle.addEventListener("change", () => {
    if (toggle.checked) preferredModels.add(toggle.dataset.model);
    else preferredModels.delete(toggle.dataset.model);
    try {
      localStorage.setItem(selectionKey, JSON.stringify([...preferredModels]));
    } catch (_) {}
  });

for (const button of modelButtons)
  button.addEventListener("click", async () => {
    if (processing || modelBusy || !config) return;
    const key = button.dataset.modelAction;
    const action = config.model_states[key].loaded ? "unload" : "load";
    modelBusy = { key, action };
    initializeSelection();
    const feedback = $("#model-feedback");
    feedback.hidden = false;
    feedback.classList.remove("error");
    feedback.textContent =
      action === "unload" ? "正在释放模型会话…" : "正在加载本地模型…";
    try {
      const response = await fetch(`/api/models/${key}/${action}`, {
        method: "POST",
      });
      const payload = await response.json();
      if (!response.ok)
        throw new Error(payload.detail || "模型操作失败，请重试。");
      applyStatus(payload);
      feedback.textContent =
        action === "unload"
          ? "模型已卸载，文件保留；需要时可重新加载。"
          : "模型已加载，已恢复之前的开关选择。";
    } catch (error) {
      feedback.classList.add("error");
      feedback.textContent =
        error.message === "Failed to fetch"
          ? "无法连接后台，请确认服务仍在运行。"
          : error.message;
      await refreshStatus();
    } finally {
      modelBusy = null;
      initializeSelection();
    }
  });
const percent = (value) => `${(value * 100).toFixed(2)}%`;
const fileSize = (bytes) =>
  bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
    : `${Math.ceil(bytes / 1024)} KiB`;

function updateToolbar() {
  updateModelButtons();
  $("#result-count").textContent = records.length;
  $("#empty-state").hidden = records.length > 0;
  $("#clear-button").disabled = records.length === 0 || processing;
  $("#export-button").disabled = !records.some((record) => record.result);
  const remaining = records.filter((record) =>
    ["queued", "running"].includes(record.status),
  ).length;
  const completed = records.filter((record) => record.status === "done").length;
  $("#queue-status").hidden = records.length === 0;
  $("#queue-status").textContent =
    remaining > 0
      ? `正在依次分析 · 已完成 ${completed} 张 · 剩余 ${remaining} 张`
      : `本次分析完成 · ${completed} 张成功${records.length > completed ? ` · ${records.length - completed} 张失败` : ""}`;
}

function setState(record, status, message) {
  record.status = status;
  record.card.dataset.status = status;
  const state = $(".card-state", record.card);
  state.className = `card-state ${status}`;
  state.textContent = {
    queued: "等待分析",
    running: "分析中",
    done: "已完成",
    failed: "未完成",
  }[status];
  $(".card-placeholder p", record.card).textContent = message || "";
  if (status === "failed")
    $(".card-placeholder", record.card).classList.add("failed");
  updateToolbar();
}

function scoreRows(target, scores) {
  const best = Math.max(...Object.values(scores));
  target.replaceChildren();
  if (!Object.keys(scores).length) {
    const note = document.createElement("p");
    note.className = "disabled-score-note";
    note.textContent = "本图未启用此模型";
    target.append(note);
    return;
  }
  for (const [label, probability] of Object.entries(scores)) {
    const row = document.createElement("div");
    row.className = `score-row${probability === best ? " top-score" : ""}`;
    row.dataset.label = label;
    const name = document.createElement("span");
    name.className = "score-label";
    name.textContent = label;
    const value = document.createElement("span");
    value.className = "score-value";
    value.textContent = percent(probability);
    value.title = `原始概率：${probability.toFixed(8)}`;
    const track = document.createElement("div");
    track.className = "score-track";
    track.setAttribute("role", "meter");
    track.setAttribute("aria-label", label);
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", String(probability * 100));
    const fill = document.createElement("div");
    fill.className = "score-fill";
    fill.style.width = percent(probability);
    track.append(fill);
    row.append(name, value, track);
    target.append(row);
  }
}

function renderResult(record, payload) {
  record.result = payload;
  const result = payload.classification;
  const card = record.card;
  $(".card-placeholder", card).hidden = true;
  $(".score-content", card).hidden = false;
  const hasDimension = result.anime_probability !== null;
  $(".dimension-summary", card).hidden = !hasDimension;
  $(".dimension-bar", card).hidden = !hasDimension;
  $(".dimension-source", card).textContent = hasDimension
    ? `汇总来源：${result.dimension_model === "anime_real" ? "anime_real_cls" : "real-or-anime-age"}`
    : "本图未启用二次元 / 三次元分类模型";
  $(".anime-probability", card).textContent = hasDimension
    ? percent(result.anime_probability)
    : "";
  $(".real-probability", card).textContent = percent(
    1 - result.anime_probability,
  );
  $(".dimension-bar > span", card).style.width = percent(
    result.anime_probability,
  );
  scoreRows($(".anime-rows", card), result.anime_scores);
  scoreRows($(".anime-real-rows", card), result.anime_real_scores || {});
  scoreRows($(".nsfw-rows", card), result.nsfw_scores);
  $(".image-meta", card).textContent =
    `${result.width} × ${result.height} · ${fileSize(record.file.size)} · ${result.image_format.toUpperCase()}`;
  const badges = $(".prediction-badges", card);
  for (const [text, className] of [
    [
      result.dimension === "unknown"
        ? "未分类"
        : result.dimension === "anime"
          ? "二次元"
          : "非二次元",
      "",
    ],
    [
      result.safety === "unknown" ? "NSFW 未检测" : result.safety.toUpperCase(),
      result.safety === "unknown"
        ? ""
        : result.safety === "normal"
          ? "safe"
          : "unsafe",
    ],
  ]) {
    const badge = document.createElement("span");
    badge.textContent = text;
    badge.className = className;
    badges.append(badge);
  }
  const frames =
    result.total_frames > 1
      ? ` · 动图 ${result.total_frames} 帧，抽样 ${result.frames.length} 帧${result.nsfw_probability !== null ? "；NSFW 取最高风险帧" : ""}`
      : "";
  $(".result-detail", card).textContent =
    `分析耗时 ${(payload.elapsed_ms / 1000).toFixed(2)} s${result.nsfw_probability !== null ? ` · NSFW 阈值 ${percent(payload.thresholds.nsfw)}` : ""}${frames}`;
  setState(record, "done");
}

async function processQueue() {
  if (processing || !config?.ready) return;
  processing = true;
  updateToolbar();
  try {
    let record;
    while ((record = records.find((item) => item.status === "queued"))) {
      setState(
        record,
        "running",
        `正在运行 ${record.modelKeys.length} 个模型…`,
      );
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 180000);
      try {
        const response = await fetch(
          `/api/classify?models=${encodeURIComponent(record.modelKeys.join(","))}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/octet-stream" },
            body: record.file,
            signal: controller.signal,
          },
        );
        const payload = await response.json();
        if (!response.ok)
          throw new Error(payload.detail || "分析失败，请重试。");
        renderResult(record, payload);
      } catch (error) {
        setState(
          record,
          "failed",
          error.name === "AbortError"
            ? "分析超时，请重新添加这张图片。"
            : error.message === "Failed to fetch"
              ? "无法连接后台，请确认服务仍在运行。"
              : error.message,
        );
      } finally {
        window.clearTimeout(timeout);
      }
    }
  } finally {
    processing = false;
    updateToolbar();
  }
}

function applyStatus(payload) {
  config = payload;
  initializeSelection();
  const loadedCount = Object.keys(config.models).length;
  $("#model-status").textContent = config.ready
    ? `${loadedCount} 个模型已就绪`
    : config.error
      ? "模型未就绪"
      : "全部模型已卸载";
  $("#status-dot").className =
    `runtime-dot ${config.ready ? "ready" : "error"}`;
  $("#upload-hint").textContent =
    `支持批量上传 · 每张最多 ${fileSize(config.max_upload_bytes)}`;
  $("#upload-error").hidden = config.ready;
  if (!config.ready)
    $("#upload-error").textContent =
      config.error || "请先在下方加载至少一个模型，再添加图片。";
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error("无法连接后台。");
    applyStatus(await response.json());
    return config.ready;
  } catch (_error) {
    $("#model-status").textContent = "后台连接失败";
    $("#status-dot").className = "runtime-dot error";
    $("#upload-error").textContent = "无法连接后台，请确认服务已启动后重试。";
    $("#upload-error").hidden = false;
    return false;
  }
}

async function addFiles(files) {
  if (!files.length) return;
  if (modelBusy) {
    $("#upload-error").textContent = "模型操作进行中，请完成后再添加图片。";
    $("#upload-error").hidden = false;
    return;
  }
  $("#upload-error").hidden = true;
  if (!config?.ready && !(await refreshStatus())) return;
  const modelKeys = selectedModels();
  if (!modelKeys.length) {
    $("#upload-error").textContent = "请至少启用一个模型，再添加图片。";
    $("#upload-error").hidden = false;
    return;
  }
  for (const file of files) {
    const card =
      $("#result-template").content.firstElementChild.cloneNode(true);
    const record = {
      id: ++counter,
      file,
      card,
      status: "queued",
      result: null,
      url: null,
      modelKeys: modelKeys.slice(),
    };
    $(".file-number", card).textContent = String(record.id).padStart(2, "0");
    $(".file-name", card).textContent = file.name;
    $(".file-name", card).title = file.name;
    $(".image-meta", card).textContent = fileSize(file.size);
    const isImage =
      file.type.startsWith("image/") ||
      /\.(png|jpe?g|gif|webp|bmp|tiff?|avif|hei[cf])$/i.test(file.name);
    if (isImage && file.size <= config.max_upload_bytes && file.size > 0) {
      record.url = URL.createObjectURL(file);
      $("img", card).src = record.url;
      $("img", card).addEventListener(
        "error",
        () => {
          $("img", card).hidden = true;
          $(".image-preview", card).textContent = "暂无预览";
        },
        { once: true },
      );
    } else {
      $("img", card).hidden = true;
    }
    records.push(record);
    $("#results").append(card);
    if (!isImage)
      setState(record, "failed", "这个文件不是图片，请选择图片文件。");
    else if (file.size > config.max_upload_bytes)
      setState(
        record,
        "failed",
        `图片过大，每张最多 ${fileSize(config.max_upload_bytes)}。`,
      );
    else if (!file.size)
      setState(record, "failed", "图片文件为空，请重新选择。");
    else setState(record, "queued", "图片已加入队列");
  }
  processQueue();
}

input.addEventListener("change", () => {
  addFiles(Array.from(input.files));
  input.value = "";
});
dropzone.addEventListener("keydown", (event) => {
  if (["Enter", " "].includes(event.key)) {
    event.preventDefault();
    input.click();
  }
});
document.addEventListener("dragenter", (event) => {
  if (!Array.from(event.dataTransfer.types).includes("Files")) return;
  event.preventDefault();
  dragDepth++;
  dropzone.classList.add("dragging");
});
document.addEventListener("dragover", (event) => {
  if (Array.from(event.dataTransfer.types).includes("Files"))
    event.preventDefault();
});
document.addEventListener("dragleave", (event) => {
  event.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropzone.classList.remove("dragging");
});
document.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  dropzone.classList.remove("dragging");
  addFiles(Array.from(event.dataTransfer.files));
});
$("#clear-button").addEventListener("click", () => {
  if (processing) return;
  for (const record of records) if (record.url) URL.revokeObjectURL(record.url);
  records.length = 0;
  counter = 0;
  $("#results").replaceChildren();
  updateToolbar();
});
$("#export-button").addEventListener("click", () => {
  const payload = records
    .filter((record) => record.result)
    .map((record) => ({ filename: record.file.name, ...record.result }));
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "kanami-image-results.json";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener("beforeunload", () => {
  for (const record of records) if (record.url) URL.revokeObjectURL(record.url);
});
refreshStatus();
