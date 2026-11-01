(() => {
  "use strict";

  const views = ["overview", "settings", "resources", "polls", "sources"];
  const titles = {
    overview: "总览",
    settings: "插件设置",
    resources: "缓存与资源",
    polls: "轮询记录",
    sources: "数据来源",
  };
  const pluginId = "astrbot_plugin_bluearchive_toolbox";
  const bridge = window.AstrBotPluginView;
  const state = {
    settings: null,
    dirty: false,
    saving: false,
    cacheUsageLoading: false,
    pollArchiveLoading: false,
    pollDate: "",
  };
  const byId = (id) => document.getElementById(id);
  let hostIsDark = false;
  let manualTheme = null;

  try {
    const savedTheme = window.localStorage.getItem("ba-toolbox-theme");
    if (savedTheme === "light" || savedTheme === "dark") manualTheme = savedTheme;
  } catch {
    manualTheme = null;
  }

  function applyTheme() {
    const theme = manualTheme || (hostIsDark ? "dark" : "light");
    const isDark = theme === "dark";
    document.documentElement.dataset.theme = theme;
    const toggle = byId("theme-toggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", String(isDark));
      toggle.setAttribute("aria-label", isDark ? "切换浅色模式" : "切换暗色模式");
      byId("theme-toggle-glyph").setAttribute("href", isDark ? "#icon-sun" : "#icon-moon");
      toggle.querySelector(".theme-toggle-label").textContent = isDark ? "浅色" : "暗色";
    }

    const mascot = byId("mascot-host");
    if (mascot) {
      const character = isDark ? "plana" : "arona";
      mascot.dataset.character = character;
      mascot.setAttribute("aria-label", isDark ? "普拉娜" : "阿洛娜");
      document.dispatchEvent(
        new CustomEvent("ba-toolbox:character-change", {
          detail: { character, host: mascot },
        }),
      );
    }
  }

  function showView(name) {
    if (!views.includes(name)) return;
    document.querySelectorAll(".view").forEach((view) => {
      view.classList.toggle("active", view.id === `view-${name}`);
    });
    document.querySelectorAll(".nav-item").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === name);
    });
    byId("current-section").textContent = titles[name];
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (name === "settings" && state.settings === null) void loadSettings();
    if (name === "resources") void loadCacheUsage();
    if (name === "polls") void loadPollArchive();
  }

  function setStatus(message, kind = "") {
    const status = byId("settings-status");
    status.textContent = message;
    status.dataset.kind = kind;
  }

  async function checkPluginStatus() {
    const badge = byId("plugin-status");
    const label = byId("plugin-status-text");
    if (!bridge) {
      label.textContent = "插件状态未知";
      badge.classList.add("unknown");
      return;
    }

    try {
      await bridge.apiGet("/settings");
      label.textContent = "插件状态正常";
      badge.classList.remove("failed", "unknown");
      badge.removeAttribute("title");
    } catch (error) {
      label.textContent = "插件状态异常";
      badge.classList.remove("unknown");
      badge.classList.add("failed");
      badge.title = error instanceof Error ? error.message : String(error);
    }
  }

  function createField(groupName, key, schema, value) {
    const field = document.createElement("label");
    field.className = "config-field";
    const title = document.createElement("span");
    title.className = "config-field-title";
    title.textContent = schema.description || key;
    field.append(title);

    let input;
    const kind = schema.type;
    if (kind === "bool") {
      const control = document.createElement("span");
      control.className = "config-toggle";
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(value);
      control.append(input);
      field.append(control);
    } else if (
      kind === "list" &&
      schema.items &&
      typeof schema.items === "object" &&
      !schema.items.type
    ) {
      input = document.createElement("textarea");
      input.rows = Math.max(7, Array.isArray(value) ? value.length + 2 : 7);
      input.value = JSON.stringify(Array.isArray(value) ? value : [], null, 2);
      input.placeholder = '[\n  { "label": "显示文本", "command": "触发命令" }\n]';
      field.classList.add("config-field-wide");
      field.append(input);
    } else if (kind === "list") {
      input = document.createElement("textarea");
      input.rows = Math.min(7, Math.max(3, (Array.isArray(value) ? value.length : 0) + 1));
      input.value = Array.isArray(value) ? value.join("\n") : "";
      input.placeholder = "每行填写一项";
      field.classList.add("config-field-wide");
      field.append(input);
    } else if (kind === "string" && Array.isArray(schema.options)) {
      input = document.createElement("select");
      schema.options.forEach((option) => {
        const item = document.createElement("option");
        item.value = option;
        item.textContent = option;
        input.append(item);
      });
      input.value = value ?? schema.default ?? "";
      field.append(input);
    } else {
      input = document.createElement("input");
      input.type = kind === "int" ? "number" : "text";
      input.value = value ?? "";
      if (kind === "int") input.step = "1";
      field.append(input);
    }

    input.dataset.group = groupName;
    input.dataset.key = key;
    input.dataset.type =
      kind === "list" &&
      schema.items &&
      typeof schema.items === "object" &&
      !schema.items.type
        ? "json-list"
        : kind || "string";
    if (schema.hint) {
      const hint = document.createElement("small");
      hint.className = "config-field-hint";
      hint.textContent = schema.hint;
      field.append(hint);
    }
    input.addEventListener("input", markDirty);
    input.addEventListener("change", markDirty);
    return field;
  }

  function renderSettings(payload) {
    state.settings = payload;
    const host = byId("settings-groups");
    host.replaceChildren();
    Object.entries(payload.groups || {}).forEach(([groupName, group], index) => {
      const section = document.createElement("details");
      section.className = "panel settings-group";
      section.open = index === 0;
      section.dataset.group = groupName;
      section.dataset.available = String(group.available !== false);
      const summary = document.createElement("summary");
      const title = document.createElement("span");
      title.textContent = group.description || groupName;
      const mark = document.createElement("span");
      mark.className = "settings-group-mark";
      mark.textContent = String(index + 1).padStart(2, "0");
      summary.append(title, mark);
      section.append(summary);
      if (group.hint) {
        const hint = document.createElement("p");
        hint.className = group.available === false
          ? "settings-group-hint settings-group-hint-warning"
          : "settings-group-hint";
        hint.textContent = group.hint;
        section.append(hint);
      }

      const fields = document.createElement("div");
      fields.className = "config-field-grid";
      Object.entries(group.items || {}).forEach(([key, schema]) => {
        if (schema.invisible) return;
        const field = createField(groupName, key, schema, group.values?.[key]);
        if (group.available === false) {
          field.querySelector("input, select, textarea").disabled = true;
        }
        fields.append(field);
      });
      section.append(fields);
      host.append(section);
    });
    state.dirty = false;
    byId("save-settings").disabled = true;
    setStatus("已从 AstrBot 读取配置", "success");
  }

  function markDirty() {
    state.dirty = true;
    byId("save-settings").disabled = state.saving;
    setStatus("有未保存的更改", "dirty");
  }

  function collectSettings() {
    const groups = {};
    Object.entries(state.settings.groups).forEach(([groupName, group]) => {
      groups[groupName] =
        group.available === false ? {} : { ...(group.values || {}) };
    });
    document.querySelectorAll("#settings-groups [data-group][data-key]").forEach((input) => {
      const { group, key, type } = input.dataset;
      let value;
      if (type === "bool") value = input.checked;
      else if (type === "list") {
        value = input.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      } else if (type === "json-list") {
        try {
          value = JSON.parse(input.value);
        } catch {
          const label = input.closest(".config-field").querySelector(".config-field-title").textContent;
          throw new Error(`${label} 必须是有效的 JSON`);
        }
        if (!Array.isArray(value)) {
          const label = input.closest(".config-field").querySelector(".config-field-title").textContent;
          throw new Error(`${label} 必须是 JSON 数组`);
        }
      } else if (type === "int") {
        const rawValue = input.value.trim();
        value = Number(rawValue);
        if (!rawValue || !Number.isSafeInteger(value)) {
          throw new Error(`${input.closest(".config-field").querySelector(".config-field-title").textContent} 必须填写有效整数`);
        }
      } else value = input.value;
      groups[group][key] = value;
    });
    return { groups };
  }

  async function loadSettings() {
    if (!bridge) {
      setStatus("AstrBot 页面桥接尚未就绪，请刷新插件页面", "error");
      return;
    }
    setStatus("正在读取配置…");
    byId("reload-settings").disabled = true;
    try {
      const result = await bridge.apiGet("/settings");
      renderSettings(result);
    } catch (error) {
      setStatus(`配置读取失败：${error.message}`, "error");
    } finally {
      byId("reload-settings").disabled = false;
    }
  }

  async function saveSettings() {
    if (!bridge || state.saving || !state.settings) return;
    state.saving = true;
    byId("save-settings").disabled = true;
    byId("reload-settings").disabled = true;
    setStatus("正在保存到 AstrBot…");
    try {
      await bridge.apiPost("/settings", collectSettings());
      state.dirty = false;
      await loadSettings();
      setStatus("配置已保存到 AstrBot", "success");
    } catch (error) {
      setStatus(`保存失败：${error.message}`, "error");
    } finally {
      state.saving = false;
      byId("reload-settings").disabled = false;
      byId("save-settings").disabled = !state.dirty;
    }
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes;
    let unitIndex = -1;
    do {
      value /= 1024;
      unitIndex += 1;
    } while (value >= 1024 && unitIndex < units.length - 1);
    return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
  }

  function renderCacheUsage(payload) {
    const categories = payload.categories || [];
    const total = byId("cache-usage-total");
    const cardLimit = byId("cache-usage-card-limit");
    const cardCount = byId("cache-usage-card-count");
    const fileCount = byId("cache-usage-file-count");
    const chart = byId("cache-usage-chart");
    const status = byId("cache-usage-status");
    const cardImages = categories.find((item) => item.id === "card_images");

    total.textContent = formatBytes(payload.total_bytes);
    cardLimit.textContent = cardImages
      ? `${formatBytes(cardImages.bytes)} / ${formatBytes(payload.card_cache_max_bytes)}`
      : `0 B / ${formatBytes(payload.card_cache_max_bytes)}`;
    cardCount.textContent = `${cardImages?.files || 0} / ${payload.card_cache_max_items} 张`;
    fileCount.textContent = String(payload.total_files);
    chart.replaceChildren();

    const maxBytes = Math.max(...categories.map((item) => item.bytes), 0);
    categories.forEach((item) => {
      const row = document.createElement("div");
      row.className = "usage-row";
      row.setAttribute("role", "listitem");

      const label = document.createElement("span");
      label.className = "usage-label";
      label.textContent = item.label;

      const track = document.createElement("div");
      track.className = "usage-track";
      track.setAttribute("role", "img");
      track.setAttribute(
        "aria-label",
        `${item.label}：${formatBytes(item.bytes)}，${item.files} 个文件`,
      );
      const bar = document.createElement("span");
      bar.style.width = maxBytes > 0 ? `${(item.bytes / maxBytes) * 100}%` : "0%";
      track.append(bar);

      const value = document.createElement("span");
      value.className = "usage-value";
      value.textContent = `${formatBytes(item.bytes)} · ${item.files} 个文件`;
      row.append(label, track, value);
      chart.append(row);
    });

    status.textContent = `统计于 ${new Date(payload.generated_at).toLocaleString()}`;
    status.dataset.kind = "success";
  }

  async function loadCacheUsage() {
    if (!bridge || state.cacheUsageLoading) {
      if (!bridge) {
        byId("cache-usage-status").textContent =
          "AstrBot 页面桥接尚未就绪，请刷新插件页面";
        byId("cache-usage-status").dataset.kind = "error";
      }
      return;
    }

    state.cacheUsageLoading = true;
    byId("refresh-cache-usage").disabled = true;
    byId("cache-usage-status").textContent = "正在读取缓存用量…";
    byId("cache-usage-status").dataset.kind = "";
    try {
      renderCacheUsage(await bridge.apiGet("/cache-usage"));
    } catch (error) {
      byId("cache-usage-total").textContent = "读取失败";
      byId("cache-usage-status").textContent = `缓存用量读取失败：${error.message}`;
      byId("cache-usage-status").dataset.kind = "error";
    } finally {
      state.cacheUsageLoading = false;
      byId("refresh-cache-usage").disabled = false;
    }
  }

  function pluginEndpoint(path) {
    return `/api/v1/plugins/extensions/${encodeURIComponent(pluginId)}${path}`;
  }

  function renderPollEntries(payload) {
    const host = byId("poll-archive-cards");
    const entries = Array.isArray(payload.entries) ? payload.entries : [];
    host.replaceChildren();
    byId("poll-archive-status").dataset.kind = "";
    if (!entries.length) {
      byId("poll-archive-status").textContent = "该日期暂无轮询结果。";
      return;
    }

    byId("poll-archive-status").textContent = "";
    entries.forEach((entry) => {
      const card = document.createElement("article");
      card.className = `poll-card ${entry.status === "failed" ? "failed" : ""}`;
      const heading = document.createElement("div");
      heading.className = "poll-card-heading";
      const title = document.createElement("strong");
      title.textContent = entry.label || entry.category || "轮询结果";
      const badge = document.createElement("span");
      badge.className = `poll-card-state ${entry.status === "failed" ? "failed" : "success"}`;
      badge.textContent = entry.status === "failed" ? "失败" : "已归档";
      heading.append(title, badge);
      const meta = document.createElement("p");
      const timestamp = entry.created_at ? new Date(entry.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
      const regionNames = { cn: "国服", global: "国际服", jp: "日服", all: "全服" };
      const details = [regionNames[entry.region] || entry.region, timestamp].filter(Boolean);
      if (entry.status !== "failed") details.push(`${entry.item_count || 0} 项`);
      meta.textContent = details.join(" · ");
      card.append(heading, meta);

      if (entry.image && entry.run_id) {
        const imageLink = document.createElement("a");
        imageLink.className = "poll-card-image-link";
        imageLink.href = pluginEndpoint(
          `/poll-archive/${encodeURIComponent(payload.date)}/${encodeURIComponent(entry.category)}/${encodeURIComponent(entry.run_id)}`,
        );
        imageLink.target = "_blank";
        imageLink.rel = "noopener noreferrer";
        imageLink.setAttribute("aria-label", `打开${title.textContent}卡片原图`);
        const image = document.createElement("img");
        image.loading = "lazy";
        image.alt = `${title.textContent}轮询卡片预览`;
        image.src = imageLink.href;
        image.addEventListener("error", () => {
          const error = document.createElement("p");
          error.className = "poll-card-error";
          error.textContent = "卡片图片读取失败，请刷新记录或检查插件日志。";
          imageLink.replaceWith(error);
        }, { once: true });
        imageLink.append(image);
        card.append(imageLink);
      } else {
        const error = document.createElement("p");
        error.className = "poll-card-error";
        error.textContent = entry.error || "该类别未能生成卡片，请检查插件日志。";
        card.append(error);
      }
      host.append(card);
    });
  }

  async function loadPollDay(archiveDate) {
    if (!archiveDate || !bridge) return;
    byId("poll-archive-status").textContent = "正在读取当日轮询结果…";
    byId("poll-archive-status").dataset.kind = "";
    try {
      const response = await bridge.apiGet(`/poll-archive/${encodeURIComponent(archiveDate)}`);
      renderPollEntries(response.data || response);
    } catch (error) {
      byId("poll-archive-cards").replaceChildren();
      byId("poll-archive-status").textContent = `读取轮询结果失败：${error.message}`;
      byId("poll-archive-status").dataset.kind = "error";
    }
  }

  async function loadPollArchive() {
    if (!bridge || state.pollArchiveLoading) {
      if (!bridge) {
        byId("poll-archive-summary").textContent = "AstrBot 页面桥接尚未就绪，请刷新插件页面";
      }
      return;
    }
    state.pollArchiveLoading = true;
    byId("refresh-poll-archive").disabled = true;
    byId("poll-archive-summary").textContent = "正在读取轮询记录…";
    byId("poll-archive-status").textContent = "";
    byId("poll-archive-status").dataset.kind = "";
    try {
      const response = await bridge.apiGet("/poll-archive");
      const payload = response.data || response;
      const days = Array.isArray(payload.days) ? payload.days : [];
      const select = byId("poll-archive-date");
      select.replaceChildren();
      days.forEach((day) => {
        const option = document.createElement("option");
        option.value = day.date;
        option.textContent = `${day.date} · ${day.success_count} 成功${day.failure_count ? ` · ${day.failure_count} 失败` : ""}`;
        select.append(option);
      });
      select.disabled = !days.length;
      if (!days.length) {
        state.pollDate = "";
        byId("poll-archive-summary").textContent = "暂无轮询记录；到达设置的每日轮询时间后将自动归档卡片。";
        byId("poll-archive-cards").replaceChildren();
        byId("poll-archive-status").textContent = "";
        return;
      }
      state.pollDate = days.some((day) => day.date === state.pollDate)
        ? state.pollDate
        : days[0].date;
      select.value = state.pollDate;
      const total = days.reduce((count, day) => count + day.entry_count, 0);
      byId("poll-archive-summary").textContent = `${days.length} 天 · ${total} 条轮询结果`;
      await loadPollDay(state.pollDate);
    } catch (error) {
      byId("poll-archive-summary").textContent = "读取失败";
      byId("poll-archive-status").textContent = `轮询记录读取失败：${error.message}`;
      byId("poll-archive-status").dataset.kind = "error";
    } finally {
      state.pollArchiveLoading = false;
      byId("refresh-poll-archive").disabled = false;
    }
  }

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  document.querySelectorAll("[data-go]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.go));
  });
  byId("theme-toggle").addEventListener("click", () => {
    manualTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    try {
      window.localStorage.setItem("ba-toolbox-theme", manualTheme);
    } catch {
      // Theme selection still works for the current page if storage is unavailable.
    }
    applyTheme();
  });
  byId("reload-settings").addEventListener("click", () => void loadSettings());
  byId("save-settings").addEventListener("click", () => void saveSettings());
  byId("refresh-cache-usage").addEventListener("click", () => void loadCacheUsage());
  byId("refresh-poll-archive").addEventListener("click", () => void loadPollArchive());
  byId("poll-archive-date").addEventListener("change", (event) => {
    state.pollDate = event.target.value;
    void loadPollDay(state.pollDate);
  });

  if (bridge) {
    bridge.onContext((context) => {
      if (typeof context?.isDark === "boolean") {
        hostIsDark = context.isDark;
        applyTheme();
      }
    });
  }
  applyTheme();
  void checkPluginStatus();
})();
