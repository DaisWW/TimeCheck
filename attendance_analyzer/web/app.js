"use strict";

const $ = (id) => document.getElementById(id);
const make = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const icon = (name) => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS(svg.namespaceURI, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.setAttribute("aria-hidden", "true");
  svg.append(use);
  return svg;
};
const button = (text, className, handler) => {
  const result = make("button", className, text);
  result.type = "button";
  if (handler) result.addEventListener("click", handler);
  return result;
};
const state = {
  files: { input: [], output: [], config_error: null },
  selected: { input: new Set(), output: new Set() },
  expanded: new Set(),
  anchor: {},
  busy: false,
  working: false,
  running: false,
  phase: "ready",
  runId: "",
  cursor: 0,
  log: "",
  error: "",
  connected: false,
  exited: false,
  timer: null,
  editor: null,
};

async function api(method, args = {}) {
  let response;
  try {
    response = await fetch(`api/${method}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Attendance-Client": "1",
      },
      body: JSON.stringify(args),
      signal: AbortSignal.timeout(120000),
    });
  } catch (error) {
    throw new Error(
      "无法连接本机考勤程序。请确认程序仍在运行；已输入的配置仍保留在当前页面。\n" +
        error.message,
    );
  }
  const result = await response.json();
  if (!result.ok) throw new Error(result.error);
  return result.data;
}

let toastTimer;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 3800);
}

function showError(error) {
  state.error = error.message || String(error);
  $("error-message").textContent = state.error;
  $("error-banner").hidden = false;
}

function formError(error) {
  $("editor-error").textContent = error.message || String(error);
  $("editor-error").hidden = false;
  if (state.editor?.kind === "rules") {
    for (const field of state.editor.fields) {
      if (
        String(error.message).includes(field.label) ||
        String(error.message).includes(field.key)
      ) {
        selectRuleTab(field.group);
        const input = document.querySelector(`[data-rule="${field.key}"]`);
        input.classList.add("invalid");
        input.focus();
        break;
      }
    }
  }
}

async function perform(work, form = false) {
  if (state.working) return;
  state.working = true;
  syncControls();
  try {
    return await work();
  } catch (error) {
    state.working = false;
    syncControls();
    form ? formError(error) : showError(error);
  } finally {
    state.working = false;
    syncControls();
  }
}

function flatten(entries) {
  return entries.flatMap((item) => [item, ...flatten(item.children)]);
}
function sizeText(bytes) {
  return bytes >= 1048576
    ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.ceil(bytes / 1024))} KB`;
}

function syncControls() {
  const busy = state.busy || state.working || !state.connected;
  document
    .querySelectorAll("[data-lock]")
    .forEach((control) => (control.disabled = busy));
  $("start").disabled =
    busy ||
    !!state.files.config_error ||
    !state.files.input.some((item) => item.eligible);
  for (const kind of ["input", "output"]) {
    const selected = state.selected[kind].size;
    $("remove-" + kind).disabled = busy || !selected;
    $("clear-" + kind).disabled = busy || !state.files[kind].length;
    $(kind + "-selected").textContent = selected
      ? `已选 ${selected} 项`
      : kind === "input"
        ? "全部文件"
        : "全部结果";
    const entries = flatten(state.files[kind]).filter((item) => !item.blocked);
    const checkbox = $("select-" + kind);
    checkbox.disabled = !entries.length;
    checkbox.checked =
      !!entries.length &&
      entries.every((item) => state.selected[kind].has(item.id));
    checkbox.indeterminate = selected > 0 && !checkbox.checked;
  }
  $("open-selected").disabled =
    state.selected.output.size !== 1 || !state.connected;
  $("drop-zone").setAttribute("aria-disabled", String(busy));
  $("drop-zone").tabIndex = busy ? -1 : 0;
  $("file-picker").disabled = busy;
  $("start").querySelector("span").textContent = state.running
    ? "正在统计…"
    : state.phase === "ready"
      ? "开始统计全部"
      : "再次统计全部";
  $("exit").disabled = state.running || state.working;
  if (state.editor) {
    $("editor")
      .querySelectorAll("input,select,textarea,button")
      .forEach((control) => (control.disabled = busy));
  }
}

async function refreshFiles(expandLatest = false) {
  state.files = await api("files");
  for (const kind of ["input", "output"]) {
    const available = new Set(
      flatten(state.files[kind]).map((item) => item.id),
    );
    state.selected[kind] = new Set(
      [...state.selected[kind]].filter((id) => available.has(id)),
    );
  }
  if (expandLatest && state.files.output[0]?.folder)
    state.expanded.add(state.files.output[0].id);
  renderFiles("input");
  renderFiles("output");
  $("input-count").textContent =
    `${state.files.input.filter((item) => item.eligible).length} 份待统计`;
  $("output-count").textContent = `${state.files.output.length} 个批次 / 结果`;
  if (state.files.config_error)
    showError(
      new Error(
        "请检查软件同目录的 config.json：\n" + state.files.config_error,
      ),
    );
  syncControls();
}

function renderFiles(kind) {
  const list = $(kind + "-list");
  const scroll = list.scrollTop;
  list.replaceChildren();
  if (!state.files[kind].length) {
    const empty = make("div", "empty-state");
    const art = make("div", "empty-illustration");
    art.append(icon(kind === "input" ? "upload" : "folder"));
    empty.append(
      art,
      make(
        "strong",
        "",
        kind === "input" ? "从一份原始考勤开始" : "让每一份结果，都有归处",
      ),
      make(
        "p",
        "",
        kind === "input"
          ? "将文件拖到这里，或点击上方选择文件"
          : "完成统计后，报表会按批次出现在这里",
      ),
    );
    list.append(empty);
    return;
  }
  const append = (entries, parent) =>
    entries.forEach((item) => {
      const row = make("div", "file-row");
      row.dataset.id = item.id;
      row.setAttribute("role", "listitem");
      row.tabIndex = 0;
      row.classList.toggle("selected", state.selected[kind].has(item.id));
      row.classList.toggle("blocked", item.blocked);
      const check = make("input");
      check.type = "checkbox";
      check.checked = state.selected[kind].has(item.id);
      check.disabled = item.blocked;
      check.setAttribute("aria-label", "选择 " + item.name);
      check.addEventListener("click", (event) => {
        event.stopPropagation();
        selectRow(kind, item.id, event, true);
      });
      row.append(check);
      if (item.folder && kind === "output") {
        const toggle = button("", "batch-toggle", (event) => {
          event.stopPropagation();
          state.expanded.has(item.id)
            ? state.expanded.delete(item.id)
            : state.expanded.add(item.id);
          renderFiles(kind);
        });
        toggle.setAttribute("aria-label", "展开或收起 " + item.name);
        toggle.setAttribute(
          "aria-expanded",
          String(state.expanded.has(item.id)),
        );
        toggle.append(icon("chevron"));
        row.append(toggle);
      }
      const fileIcon = make(
        "span",
        "file-icon" + (item.folder ? " folder" : ""),
      );
      fileIcon.append(icon(item.folder ? "folder" : "file"));
      const description = make("div", "file-description");
      const name = make("div", "file-name", item.name);
      name.title = item.name;
      const meta = make("div", "file-meta");
      meta.append(
        make(
          "span",
          "",
          item.folder ? `${item.children.length} 项文件` : sizeText(item.size),
        ),
        make("span", "", item.modified),
      );
      description.append(name, meta);
      row.append(fileIcon, description);
      if (kind === "input")
        row.append(
          make(
            "span",
            "file-badge" + (item.eligible ? "" : " skip"),
            item.eligible
              ? "待统计"
              : item.blocked
                ? "链接不可用"
                : "不参与统计",
          ),
        );
      const open = button("", "icon-button", (event) => {
        event.stopPropagation();
        perform(() => api("open_path", { kind, identifier: item.id }));
      });
      open.append(icon("external"));
      open.title = "打开 " + item.name;
      open.setAttribute("aria-label", open.title);
      open.disabled = item.blocked;
      row.append(open);
      row.addEventListener("click", (event) => selectRow(kind, item.id, event));
      row.addEventListener(
        "dblclick",
        () =>
          !item.blocked &&
          perform(() => api("open_path", { kind, identifier: item.id })),
      );
      row.addEventListener("keydown", (event) => {
        if (event.target !== row) return;
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          selectRow(kind, item.id, event, true);
        }
        if (event.key === "Delete" && !state.busy && !state.working)
          perform(() => removeFiles(kind));
      });
      parent.append(row);
      if (item.folder && state.expanded.has(item.id) && item.children.length) {
        const children = make("div", "batch-children");
        append(item.children, children);
        parent.append(children);
      }
    });
  append(state.files[kind], list);
  list.scrollTop = scroll;
}

function selectRow(kind, id, event, toggle = false) {
  const item = flatten(state.files[kind]).find((value) => value.id === id);
  if (item.blocked) return;
  const selected = state.selected[kind];
  const visible = [
    ...$(kind + "-list").querySelectorAll(".file-row:not(.blocked)"),
  ].map((row) => row.dataset.id);
  if (event.shiftKey && visible.includes(state.anchor[kind])) {
    const bounds = [
      visible.indexOf(state.anchor[kind]),
      visible.indexOf(id),
    ].sort((a, b) => a - b);
    if (!event.ctrlKey) selected.clear();
    visible
      .slice(bounds[0], bounds[1] + 1)
      .forEach((value) => selected.add(value));
  } else if (toggle || event.ctrlKey || event.metaKey) {
    selected.has(id) ? selected.delete(id) : selected.add(id);
    state.anchor[kind] = id;
  } else {
    selected.clear();
    selected.add(id);
    state.anchor[kind] = id;
  }
  // 保持节点和焦点，避免双击或键盘选择时重建整行。
  $(kind + "-list")
    .querySelectorAll(".file-row")
    .forEach((row) => {
      row.classList.toggle("selected", selected.has(row.dataset.id));
      row.querySelector("input").checked = selected.has(row.dataset.id);
    });
  syncControls();
}

function ask(title, message, choices, files = []) {
  return new Promise((resolve) => {
    const dialog = $("confirm");
    $("confirm-title").textContent = title;
    $("confirm-message").textContent = message;
    $("confirm-files").replaceChildren(
      ...files.map((name) => make("li", "", name)),
    );
    const finish = (value) => {
      dialog.close();
      dialog.oncancel = null;
      resolve(value);
    };
    $("confirm-actions").replaceChildren(
      ...choices.map((choice) =>
        button(choice.label, "btn " + (choice.style || ""), () =>
          finish(choice.value),
        ),
      ),
    );
    dialog.oncancel = (event) => {
      event.preventDefault();
      finish("cancel");
    };
    dialog.showModal();
    $("confirm-actions").querySelector("button").focus();
  });
}

function uploadFile(token, index, file, done, total) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `upload/${token}/${index}`);
    xhr.setRequestHeader("X-Attendance-Client", "1");
    xhr.timeout = 120000;
    xhr.upload.onprogress = (event) => {
      $("run-title").textContent = "正在添加考勤";
      $("run-status").textContent = `正在复制 ${file.name}，原始位置文件保留。`;
      const percent = event.lengthComputable
        ? Math.round((event.loaded * 100) / Math.max(1, event.total))
        : 0;
      $("progress-count").textContent = `${done + 1}/${total} · ${percent}%`;
      $("progress-fill").style.width =
        `${((done + percent / 100) * 100) / total}%`;
    };
    xhr.onload = () => {
      try {
        const result = JSON.parse(xhr.responseText);
        result.ok ? resolve() : reject(new Error(result.error));
      } catch (error) {
        reject(error);
      }
    };
    xhr.onerror = xhr.ontimeout = () =>
      reject(
        new Error("文件传输中断或超时，未完成的文件不会导入。请重新添加。"),
      );
    xhr.send(file);
  });
}

async function importFiles(files) {
  if (!files.length || state.busy || state.working) return;
  await perform(async () => {
    const plan = await api("begin_upload", {
      files: files.map((file) => ({ name: file.name, size: file.size })),
    });
    try {
      let choice = "skip";
      if (plan.duplicates.length)
        choice = await ask(
          "input 中已有同名文件",
          "请选择如何处理以下副本。源文件保留在原始位置。",
          [
            { label: "取消导入", value: "cancel" },
            { label: "跳过同名", value: "skip" },
            { label: "替换同名", value: "replace", style: "primary" },
          ],
          plan.duplicates,
        );
      if (choice === "cancel") return;
      for (let index = 0; index < files.length; index++)
        await uploadFile(plan.token, index, files[index], index, files.length);
      const result = await api("import_files", { token: plan.token, choice });
      toast(
        `已添加 ${result.copied} 份考勤${result.skipped ? `，跳过 ${result.skipped} 份同名文件` : ""}`,
      );
    } finally {
      await api("import_files", { token: plan.token, choice: "cancel" }).catch(
        () => {},
      );
      await refreshFiles();
    }
  });
  await pollOnce();
}

async function removeFiles(kind, clear = false) {
  const plan = await api("preview_recycle", {
    kind,
    identifiers: [...state.selected[kind]],
    clear,
  });
  const choice = await ask(
    clear ? `清空 ${kind}？` : "移到回收站？",
    "以下项目将移到 Windows 回收站，可从回收站恢复。\n" +
      (kind === "input"
        ? "只清理 input 中的副本，原始导出位置的文件保留。"
        : "移走文件夹会包含其中全部报表。"),
    [
      { label: "取消", value: "cancel" },
      { label: "确认移到回收站", value: "yes", style: "danger" },
    ],
    plan.names,
  );
  if (choice !== "yes") return;
  try {
    toast(`已将 ${await api("recycle", { token: plan.token })} 项移到回收站`);
  } finally {
    await refreshFiles();
  }
}

function detailsVisible(show) {
  $("details").hidden = !show;
  $("toggle-details").setAttribute("aria-expanded", String(show));
}

let pollPromise;
function pollOnce() {
  if (state.exited) return Promise.resolve();
  if (!pollPromise)
    pollPromise = updateStatus().finally(() => {
      pollPromise = null;
    });
  return pollPromise;
}

async function updateStatus() {
  if (state.exited) return;
  const result = await api("poll", {
    cursor: state.cursor,
    run_id: state.runId,
  });
  state.connected = true;
  $("connection").replaceChildren(
    make("i"),
    document.createTextNode("本机服务已连接"),
  );
  if (result.run_id !== state.runId) {
    state.log = "";
    state.runId = result.run_id;
  }
  state.log += result.log;
  state.cursor = result.cursor;
  if (result.log) {
    const log = $("log"),
      atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 50;
    log.textContent = state.log;
    if (atEnd) log.scrollTop = log.scrollHeight;
  }
  const finished =
    result.phase !== state.phase && ["success", "error"].includes(result.phase);
  state.busy = result.busy;
  state.running = result.running;
  state.phase = result.phase;
  if (!state.working) {
    $("run-card").className = "card run-card " + result.phase;
    $("run-title").textContent = {
      ready: "准备就绪",
      running: "正在统计",
      success: "统计完成",
      error: "统计未完成",
    }[result.phase];
    $("run-status").textContent = result.status;
    const percent = result.total ? (result.completed * 100) / result.total : 0;
    $("progress-fill").style.width = `${percent}%`;
    document
      .querySelector(".progress-track")
      .setAttribute("aria-valuenow", String(Math.round(percent)));
    $("progress-count").textContent = result.total
      ? `已完成 ${result.completed} / ${result.total} 份`
      : "等待添加文件";
  }
  $("open-result").disabled = !result.result || result.running;
  $("open-log").disabled = !result.log_available;
  if (result.refresh || finished) {
    await refreshFiles(true);
    if (result.phase === "error") {
      detailsVisible(true);
      showError(new Error(result.status + "\n完整原因见下方处理详情。"));
    } else
      toast(`统计完成，共 ${result.completed} 份。请核对报表中的待审核清单。`);
  }
  syncControls();
}

let pollingNow = false;
async function polling() {
  if (state.exited || pollingNow) return;
  pollingNow = true;
  clearTimeout(state.timer);
  try {
    await pollOnce();
    $("offline").hidden = true;
  } catch (error) {
    state.connected = false;
    $("connection").textContent = "本机服务连接中断";
    $("offline-title").textContent = "本机程序已停止或连接中断";
    $("offline-text").textContent =
      "请双击考勤程序重新打开。当前未保存的内容仍保留在此页面。";
    $("offline").hidden = false;
    syncControls();
  }
  pollingNow = false;
  if (!state.exited)
    state.timer = setTimeout(polling, document.hidden ? 15000 : 700);
}

function fieldInput(key, value) {
  let control;
  if (
    key.endsWith("count_non_workday_overtime") ||
    key.endsWith("rounding_mode")
  ) {
    control = make("select");
    const choices = key.endsWith("rounding_mode")
      ? ["向下取整", "向上取整", "就近取整"]
      : ["是", "否"];
    control.append(
      ...choices.map((text) => {
        const option = make("option", "", text);
        option.value = text;
        return option;
      }),
    );
  } else if (
    key.endsWith("holiday_dates") ||
    key.endsWith("invalid_punch_prefixes")
  ) {
    control = make("textarea");
    control.rows = 2;
  } else {
    control = make("input");
    control.type = "text";
  }
  control.value = value;
  control.dataset.rule = key;
  control.id = "rule-" + key;
  control.addEventListener("input", () => {
    control.classList.remove("invalid");
    updateDirty();
  });
  control.addEventListener("change", updateDirty);
  return control;
}

function renderRules() {
  const editor = state.editor,
    body = $("editor-body");
  const tabs = make("div", "rule-tabs");
  tabs.setAttribute("role", "tablist");
  const groups = [...new Set(editor.fields.map((field) => field.group))];
  for (const group of groups) {
    const tab = button(group, "rule-tab", () => selectRuleTab(group));
    tab.dataset.group = group;
    tab.setAttribute("role", "tab");
    tabs.append(tab);
  }
  body.append(tabs);
  for (const group of groups) {
    const section = make("div", "rule-group");
    section.dataset.group = group;
    section.setAttribute("role", "tabpanel");
    for (const field of editor.fields.filter(
      (field) => field.group === group,
    )) {
      const row = make("div", "rule-field"),
        label = make("label", "", field.label);
      label.htmlFor = "rule-" + field.key;
      const input = fieldInput(field.key, editor.initial[field.key]);
      const hint = make("small", "", field.help);
      hint.id = "hint-" + field.key;
      input.setAttribute("aria-describedby", hint.id);
      row.append(label, input, hint);
      section.append(row);
    }
    body.append(section);
  }
  selectRuleTab(groups[0]);
}

function selectRuleTab(group) {
  document.querySelectorAll(".rule-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.group === group);
    tab.setAttribute("aria-selected", String(tab.dataset.group === group));
  });
  document
    .querySelectorAll(".rule-group")
    .forEach((panel) => (panel.hidden = panel.dataset.group !== group));
}

const todayText = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const weekday = (value) => {
  const d = new Date(value + "T12:00:00");
  return Number.isNaN(d.getTime())
    ? "请选择日期"
    : "星期" + "日一二三四五六"[d.getDay()];
};

function renderHolidays() {
  const body = $("editor-body"),
    editor = state.editor;
  const intro = make("div", "holiday-intro");
  intro.append(
    icon("info"),
    make(
      "span",
      "",
      "全员适用 · 放假时段免考勤，实际工作按休息日加班。上午 / 下午以固定午休边界划分；法定节假日在考勤规则中设置。",
    ),
  );
  body.append(intro);
  const layout = make("div", "holiday-layout"),
    range = make("section", "range-card");
  range.append(make("h3", "", "添加日期或跨天范围"));
  const bounds = make("div", "range-fields");
  for (const [key, title] of [
    ["start", "开始日期"],
    ["end", "结束日期"],
  ]) {
    const label = make("label", "", title),
      input = make("input");
    input.type = "date";
    input.min = "0001-01-01";
    input.max = "9999-12-31";
    input.id = "range-" + key;
    input.value = todayText();
    input.addEventListener("change", () => {
      editor.rangeTouched = true;
      if (key === "start" && $("range-end").value < input.value)
        $("range-end").value = input.value;
      if (input.value) {
        editor.calendarYear = Number(input.value.slice(0, 4));
        editor.calendarMonth = Number(input.value.slice(5, 7));
      }
      editor.rangeAnchor = null;
      updateRange();
      renderCalendar();
      updateDirty();
    });
    label.append(input);
    bounds.append(label);
  }
  range.append(bounds);
  const periods = make("div", "range-periods");
  periods.setAttribute("role", "group");
  periods.setAttribute("aria-label", "统一放假时段");
  editor.period = "全天";
  for (const period of ["全天", "上午", "下午"]) {
    const control = button(period, period === "全天" ? "active" : "", () => {
      editor.period = period;
      editor.rangeTouched = true;
      periods.querySelectorAll("button").forEach((b) => {
        b.classList.toggle("active", b.textContent === period);
        b.setAttribute("aria-pressed", String(b.textContent === period));
      });
      updateDirty();
    });
    control.setAttribute("aria-pressed", String(period === "全天"));
    periods.append(control);
  }
  const note = make("label", "range-note", "统一备注（选填）"),
    noteInput = make("input");
  noteInput.id = "range-note";
  noteInput.placeholder = "例如：公司活动 / 春节假期";
  noteInput.addEventListener("input", () => {
    editor.rangeTouched = true;
    updateDirty();
  });
  note.append(noteInput);
  const preview = make("p", "range-preview");
  preview.id = "range-preview";
  const add = button("+ 加入逐日列表", "btn primary", () =>
    perform(addRange, true),
  );
  range.append(periods, note, preview, add);
  const calendar = make("div", "calendar");
  calendar.id = "range-calendar";
  range.append(calendar);
  const list = make("section", "holiday-list");
  const heading = make("div", "holiday-list-heading");
  heading.append(make("h3", "", "逐日安排"));
  const count = make("span");
  count.id = "holiday-count";
  heading.append(count);
  const rows = make("div", "holiday-rows");
  rows.id = "holiday-rows";
  list.append(heading, rows);
  layout.append(range, list);
  body.append(layout);
  const now = new Date();
  editor.calendarYear = now.getFullYear();
  editor.calendarMonth = now.getMonth() + 1;
  editor.rangeAnchor = null;
  renderHolidayRows();
  updateRange();
  renderCalendar();
}

function updateRange() {
  const start = $("range-start").value,
    end = $("range-end").value;
  const count =
    Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;
  $("range-preview").textContent =
    !start || !end
      ? "请选择完整的开始和结束日期。"
      : count <= 0
        ? "结束日期不能早于开始日期。"
        : count > 366
          ? `共 ${count} 天，单次最多 366 天。`
          : `共 ${count} 天，包含首尾和周末。加入后可逐日修改。`;
}

function renderCalendar() {
  const editor = state.editor,
    calendar = $("range-calendar");
  calendar.replaceChildren();
  const heading = make("div", "calendar-heading"),
    selectors = make("div");
  const changeMonth = (delta) => {
    let month = editor.calendarMonth + delta,
      year = editor.calendarYear;
    if (month < 1) {
      month = 12;
      year--;
    }
    if (month > 12) {
      month = 1;
      year++;
    }
    if (year < 1 || year > 9999) return;
    editor.calendarYear = year;
    editor.calendarMonth = month;
    renderCalendar();
  };
  const previous = button("‹", "icon-button", () => changeMonth(-1));
  previous.setAttribute("aria-label", "上个月");
  const next = button("›", "icon-button", () => changeMonth(1));
  next.setAttribute("aria-label", "下个月");
  const year = make("input");
  year.type = "number";
  year.min = "1";
  year.max = "9999";
  year.value = editor.calendarYear;
  year.setAttribute("aria-label", "日历年份");
  year.style.width = "70px";
  year.style.padding = "4px 5px";
  year.addEventListener("change", () => {
    const value = Number(year.value);
    if (Number.isInteger(value) && value >= 1 && value <= 9999) {
      editor.calendarYear = value;
      renderCalendar();
    } else year.value = editor.calendarYear;
  });
  const month = make("select");
  month.setAttribute("aria-label", "日历月份");
  for (let i = 1; i <= 12; i++) {
    const option = make("option", "", `${i}月`);
    option.value = i;
    month.append(option);
  }
  month.value = editor.calendarMonth;
  month.addEventListener("change", () => {
    editor.calendarMonth = Number(month.value);
    renderCalendar();
  });
  selectors.append(year, month);
  heading.append(previous, selectors, next);
  calendar.append(heading);
  const grid = make("div", "calendar-grid");
  for (const text of ["一", "二", "三", "四", "五", "六", "日"])
    grid.append(make("span", "weekday", text));
  const first = new Date(0);
  first.setFullYear(editor.calendarYear, editor.calendarMonth - 1, 1);
  first.setHours(12);
  const last = new Date(first);
  last.setMonth(last.getMonth() + 1, 0);
  for (let i = 0; i < (first.getDay() + 6) % 7; i++) grid.append(make("span"));
  const start = $("range-start").value,
    end = $("range-end").value;
  for (let day = 1; day <= last.getDate(); day++) {
    const value = `${String(editor.calendarYear).padStart(4, "0")}-${String(editor.calendarMonth).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const control = button(String(day), "calendar-day", () => {
      editor.rangeTouched = true;
      if (!editor.rangeAnchor) {
        editor.rangeAnchor = value;
        $("range-start").value = $("range-end").value = value;
      } else {
        const bounds = [editor.rangeAnchor, value].sort();
        $("range-start").value = bounds[0];
        $("range-end").value = bounds[1];
        editor.rangeAnchor = null;
      }
      updateRange();
      renderCalendar();
      updateDirty();
    });
    control.classList.toggle("range", value >= start && value <= end);
    control.classList.toggle("bound", value === start || value === end);
    control.classList.toggle(
      "scheduled",
      editor.rows.some((row) => row.date === value),
    );
    control.title = value + " " + weekday(value);
    control.setAttribute("aria-label", control.title);
    grid.append(control);
  }
  calendar.append(
    grid,
    make(
      "p",
      "range-preview",
      editor.rangeAnchor
        ? "再选一天作为结束日期，可切换月份。"
        : "先点开始日，再点结束日；紫色下划线表示已有安排。",
    ),
  );
}

function renderHolidayRows() {
  const editor = state.editor,
    list = $("holiday-rows");
  list.replaceChildren();
  $("holiday-count").textContent = `共 ${editor.rows.length} 天 · 可逐日修改`;
  if (!editor.rows.length) {
    const empty = make("div", "holiday-empty");
    empty.append(
      icon("calendar"),
      document.createTextNode("还没有公司放假安排"),
      make("br"),
      document.createTextNode("在左侧选择日期并加入列表"),
    );
    list.append(empty);
  }
  editor.rows.forEach((row, index) => {
    const line = make("div", "holiday-row"),
      dateBox = make("div", "holiday-date"),
      date = make("input");
    date.type = "date";
    date.min = "0001-01-01";
    date.max = "9999-12-31";
    date.value = row.date;
    date.setAttribute("aria-label", `第 ${index + 1} 项日期`);
    const week = make("small", "", weekday(row.date));
    date.addEventListener("change", () => {
      row.date = date.value;
      week.textContent = weekday(row.date);
      renderCalendar();
      updateDirty();
    });
    dateBox.append(date, week);
    const period = make("select");
    period.setAttribute("aria-label", `第 ${index + 1} 项时段`);
    for (const value of ["全天", "上午", "下午"]) {
      const option = make("option", "", value);
      option.value = value;
      period.append(option);
    }
    period.value = row.period;
    period.addEventListener("change", () => {
      row.period = period.value;
      updateDirty();
    });
    const note = make("input");
    note.value = row.note;
    note.placeholder = "备注";
    note.setAttribute("aria-label", `第 ${index + 1} 项备注`);
    note.addEventListener("input", () => {
      row.note = note.value;
      updateDirty();
    });
    const remove = button("", "icon-button", () => {
      editor.rows.splice(index, 1);
      renderHolidayRows();
      renderCalendar();
      updateDirty();
    });
    remove.setAttribute("aria-label", "移除安排 " + row.date);
    remove.title = "移除此条未保存的安排";
    remove.append(icon("close"));
    line.append(dateBox, period, note, remove);
    list.append(line);
  });
}

async function addRange() {
  const editor = state.editor;
  const values = await api("add_holiday_range", {
    rows: editor.rows,
    start: $("range-start").value,
    end: $("range-end").value,
    period: editor.period,
    note: $("range-note").value,
  });
  editor.rows = values;
  editor.rangeTouched = false;
  $("editor-error").hidden = true;
  renderHolidayRows();
  renderCalendar();
  updateDirty();
  toast("已加入逐日列表，核对后保存即可生效");
}

function editorValues() {
  if (state.editor.kind === "holidays") return state.editor.rows;
  return Object.fromEntries(
    [...$("editor-body").querySelectorAll("[data-rule]")].map((control) => [
      control.dataset.rule,
      control.value,
    ]),
  );
}
function isDirty() {
  return (
    !!state.editor &&
    (JSON.stringify(editorValues()) !== JSON.stringify(state.editor.initial) ||
      !!state.editor.rangeTouched)
  );
}
function updateDirty() {
  $("save-hint").textContent = isDirty()
    ? "有未保存的修改"
    : "配置已与本机保存内容同步";
}

async function openEditor(kind) {
  const data = await api("get_settings", { kind });
  state.editor = {
    kind,
    initial: data.values,
    revision: data.revision,
    fields: data.fields,
    rows: structuredClone(data.values),
    rangeTouched: false,
  };
  $("editor-title").textContent = kind === "rules" ? "考勤规则" : "公司放假";
  $("editor-error").hidden = true;
  $("editor-body").replaceChildren();
  kind === "rules" ? renderRules() : renderHolidays();
  updateDirty();
  $("editor").showModal();
}

async function saveEditor() {
  if (state.editor.rangeTouched) {
    const choice = await ask(
      "日期范围尚未加入列表",
      "请选择是否将左侧日期范围加入后保存。",
      [
        { label: "继续编辑", value: "cancel" },
        { label: "仅保存逐日列表", value: "skip" },
        { label: "加入并保存", value: "add", style: "primary" },
      ],
    );
    if (choice === "cancel") return false;
    if (choice === "add") await addRange();
  }
  await api("save_settings", {
    kind: state.editor.kind,
    values: editorValues(),
    revision: state.editor.revision,
  });
  $("editor").close();
  state.editor = null;
  toast("配置已保存，下次统计使用新设置");
  await refreshFiles();
  return true;
}

async function cancelEditor() {
  if (state.working) return;
  if (isDirty()) {
    const choice = await ask(
      "保留这次修改吗？",
      "未保存的修改尚未写入 config.json。",
      [
        { label: "继续编辑", value: "cancel" },
        { label: "放弃修改", value: "discard" },
        { label: "保存并关闭", value: "save", style: "primary" },
      ],
    );
    if (choice === "cancel") return;
    if (choice === "save") {
      await perform(saveEditor, true);
      return;
    }
  }
  $("editor").close();
  state.editor = null;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch (_) {
    const area = make("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (copied) toast("已复制");
    else showError(new Error("浏览器未允许复制，请选中处理详情后按 Ctrl+C。"));
  }
}

$("refresh").onclick = () => perform(refreshFiles);
$("edit-rules").onclick = () => perform(() => openEditor("rules"));
$("edit-holidays").onclick = () => perform(() => openEditor("holidays"));
$("save-editor").onclick = () => perform(saveEditor, true);
$("close-editor").onclick = $("cancel-editor").onclick = cancelEditor;
$("editor").addEventListener("cancel", (event) => {
  event.preventDefault();
  cancelEditor();
});
$("dismiss-error").onclick = () => ($("error-banner").hidden = true);
$("copy-error").onclick = () => copyText(state.error);
$("copy-log").onclick = () => copyText(state.log);
$("toggle-details").onclick = () => detailsVisible($("details").hidden);
$("open-log").onclick = () => perform(() => api("open_path", { kind: "log" }));
$("open-result").onclick = () =>
  perform(() => api("open_path", { kind: "result" }));
$("open-selected").onclick = () =>
  perform(() =>
    api("open_path", {
      kind: "output",
      identifier: [...state.selected.output][0],
    }),
  );
$("start").onclick = () =>
  perform(async () => {
    $("error-banner").hidden = true;
    detailsVisible(false);
    await api("start");
    await pollOnce();
  });
for (const kind of ["input", "output"]) {
  $("open-" + kind).onclick = () => perform(() => api("open_path", { kind }));
  $("remove-" + kind).onclick = () => perform(() => removeFiles(kind));
  $("clear-" + kind).onclick = () => perform(() => removeFiles(kind, true));
  $("select-" + kind).onchange = (event) => {
    state.selected[kind] = new Set(
      event.target.checked
        ? flatten(state.files[kind])
            .filter((item) => !item.blocked)
            .map((item) => item.id)
        : [],
    );
    renderFiles(kind);
    syncControls();
  };
}
const chooseFiles = () => {
  if (!state.busy && !state.working && state.connected)
    $("file-picker").click();
};
$("drop-zone").onclick = chooseFiles;
$("drop-zone").onkeydown = (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    chooseFiles();
  }
};
$("file-picker").onchange = (event) => {
  const files = [...event.target.files];
  event.target.value = "";
  importFiles(files);
};
for (const eventName of ["dragover", "drop"])
  document.addEventListener(eventName, (event) => event.preventDefault());
$("input-panel").addEventListener("dragover", (event) => {
  event.preventDefault();
  if (!state.busy && !state.working) $("drop-zone").classList.add("dragover");
});
$("input-panel").addEventListener("dragleave", (event) => {
  if (!$("input-panel").contains(event.relatedTarget))
    $("drop-zone").classList.remove("dragover");
});
$("input-panel").addEventListener("drop", (event) => {
  event.preventDefault();
  $("drop-zone").classList.remove("dragover");
  importFiles([...event.dataTransfer.files]);
});
$("exit").onclick = () =>
  perform(async () => {
    if (
      (await ask("退出考勤程序？", "本机服务会停止，文件和已保存配置保留。", [
        { label: "取消", value: "cancel" },
        { label: "退出程序", value: "yes", style: "primary" },
      ])) !== "yes"
    )
      return;
    await api("close");
    state.exited = true;
    clearTimeout(state.timer);
    $("offline-title").textContent = "程序已退出";
    $("offline-text").textContent =
      "可以关闭此标签页，下次双击程序即可重新打开。";
    $("offline").hidden = false;
    $("reconnect").hidden = true;
  });
$("reconnect").onclick = () => {
  clearTimeout(state.timer);
  polling();
};
document.querySelector(".nav-item.active").onclick = () =>
  window.scrollTo({ top: 0, behavior: "smooth" });
window.addEventListener("beforeunload", (event) => {
  if (isDirty() || state.working || state.running) {
    event.preventDefault();
    event.returnValue = "";
  }
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !state.exited) {
    clearTimeout(state.timer);
    polling();
  }
});

(async () => {
  try {
    await pollOnce();
    await refreshFiles();
  } catch (error) {
    showError(error);
  }
  polling();
})();
