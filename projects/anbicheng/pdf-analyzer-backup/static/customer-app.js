(function () {
  "use strict";

  var state = {
    files: [],
    configs: [],
    selectedConfig: "",
    currentJobId: "",
    currentBatchId: "",
    queueJobs: [],
    activeResultJobId: "",
    pollIntervalMs: 2000,
    result: null,
    resultDirty: false,
    regenerating: false,
    databaseTable: null,
    databaseLoaded: false,
    databasePage: 1,
    databaseSize: 20,
    databasePages: 1,
    expandedLineItems: {},
    pollTimer: null,
    fakeProgressTimer: null,
    progress: 0
  };

  var allowedExts = [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"];
  var hiddenPriceRowTerms = ["采购订单含税单价", "采购订单单价", "采购订单明细"];

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatValue(value) {
    if (value == null) return "";
    return String(value);
  }

  function formatSize(bytes) {
    if (!bytes) return "0 B";
    var units = ["B", "KB", "MB", "GB"];
    var size = bytes;
    var unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size = size / 1024;
      unit += 1;
    }
    return (unit === 0 ? size : size.toFixed(1)) + " " + units[unit];
  }

  function showView(name) {
    ["upload-view", "process-view", "result-view", "database-view"].forEach(function (id) {
      $(id).classList.toggle("hidden", id !== name);
    });
    if ($("queue-section")) {
      $("queue-section").classList.toggle("hidden", name === "database-view" || state.queueJobs.length === 0);
    }
    setActiveTab(name === "database-view" ? "database" : "workbench");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setActiveTab(name) {
    if (!$("tab-workbench") || !$("tab-database")) return;
    $("tab-workbench").classList.toggle("active", name === "workbench");
    $("tab-database").classList.toggle("active", name === "database");
  }

  function openWorkbench() {
    showView(state.result ? "result-view" : "upload-view");
  }

  function openDatabaseView() {
    showView("database-view");
    if (!state.databaseLoaded) loadProducts();
  }

  function setProgress(percent, message) {
    state.progress = Math.max(0, Math.min(100, percent));
    $("progress-bar").style.width = state.progress + "%";
    $("process-percent").textContent = state.progress + "%";
    if (message) $("process-message").textContent = message;
  }

  function startFakeProgress() {
    clearInterval(state.fakeProgressTimer);
    setProgress(8, "文件已提交，正在排队处理。");
    state.fakeProgressTimer = setInterval(function () {
      if (state.progress < 82) {
        setProgress(state.progress + Math.ceil(Math.random() * 4), "AI 正在识别字段并执行校验。");
      }
    }, 1600);
  }

  function stopFakeProgress() {
    clearInterval(state.fakeProgressTimer);
    state.fakeProgressTimer = null;
  }

  function validFile(file) {
    var lower = file.name.toLowerCase();
    var ext = lower.slice(lower.lastIndexOf("."));
    return allowedExts.indexOf(ext) !== -1;
  }

  function renderFiles() {
    var list = $("file-list");
    $("start-button").disabled = state.files.length === 0 || !state.selectedConfig;
    if (!state.files.length) {
      list.innerHTML = "";
      return;
    }
    list.innerHTML = state.files.map(function (file, index) {
      return (
        '<div class="file-item">' +
          "<div><strong>" + escapeHtml(file.name) + "</strong><span>" + formatSize(file.size) + "</span></div>" +
          '<button class="remove-file" type="button" data-index="' + index + '" aria-label="移除文件">×</button>' +
        "</div>"
      );
    }).join("");
    list.querySelectorAll(".remove-file").forEach(function (button) {
      button.addEventListener("click", function () {
        state.files.splice(Number(button.dataset.index), 1);
        renderFiles();
      });
    });
  }

  function addFiles(fileList) {
    var incoming = Array.from(fileList || []).filter(validFile);
    if (!incoming.length) {
      alert("请选择 PDF 或常见图片格式文件。");
      return;
    }
    var names = new Set(state.files.map(function (file) { return file.name + ":" + file.size; }));
    incoming.forEach(function (file) {
      var key = file.name + ":" + file.size;
      if (!names.has(key)) {
        state.files.push(file);
        names.add(key);
      }
    });
    renderFiles();
  }

  async function loadProfile() {
    var me = await window.Auth.fetchMe();
    if (!me) return;
    $("user-chip").textContent = me.display_name || me.username;
    var isAdmin = ["root_admin", "super_admin", "company_owner"].indexOf(me.role) !== -1;
    $("admin-link").style.display = isAdmin ? "inline-grid" : "none";
  }

  async function loadConfigs() {
    var response = await fetch("/api/customer/configs");
    var data = await response.json();
    state.configs = data.configs || [];
    $("config-count").textContent = "配置 " + state.configs.length;
    var select = $("config-select");
    if (!state.configs.length) {
      select.innerHTML = '<option value="">未找到配置</option>';
      $("config-desc").textContent = "请检查 config/customer_keywords.json。";
      return;
    }
    select.innerHTML = state.configs.map(function (config) {
      return '<option value="' + escapeHtml(config.id) + '">' + escapeHtml(config.label) + "</option>";
    }).join("");
    var defaultConfig = state.configs.find(function (config) { return config.is_default; }) || state.configs[0];
    select.value = defaultConfig.id;
    state.selectedConfig = defaultConfig.id;
    renderConfigDescription();
    renderFiles();
  }

  function renderConfigDescription() {
    var config = state.configs.find(function (item) { return item.id === state.selectedConfig; });
    $("config-desc").textContent = config
      ? (config.description || ("字段数量：" + config.fields_count))
      : "请选择客户关键词配置。";
  }

  async function loadHistory() {
    try {
      var response = await fetch("/api/customer/history");
      if (!response.ok) throw new Error(response.statusText);
      var jobs = await response.json();
      $("history-count").textContent = "最近任务 " + jobs.length;
      var list = $("recent-list");
      if (!jobs.length) {
        list.innerHTML = '<div class="empty-state">还没有客户关键词处理记录。</div>';
        return;
      }
      list.innerHTML = jobs.map(function (job) {
        var status = job.status === "done" ? "完成" : job.status === "failed" ? "失败" : "处理中";
        return (
          '<div class="recent-item">' +
            "<div><strong>" + escapeHtml(job.filename) + "</strong>" +
            "<span>" + status + " · " + job.fields_filled + "/" + job.fields_total + " 字段</span></div>" +
            '<button class="text-button recent-open" type="button" data-id="' + escapeHtml(job.file_id) + '">查看</button>' +
          "</div>"
        );
      }).join("");
      list.querySelectorAll(".recent-open").forEach(function (button) {
        button.addEventListener("click", function () {
          openExistingJob(button.dataset.id);
        });
      });
    } catch (error) {
      $("recent-list").innerHTML = '<div class="empty-state">最近任务读取失败。</div>';
    }
  }

  async function startProcessing() {
    if (!state.files.length || !state.selectedConfig) return;
    var formData = new FormData();
    state.files.forEach(function (file) { formData.append("pdf_files", file); });
    formData.append("config_id", state.selectedConfig);

    showView("process-view");
    $("process-title").textContent = "正在提交 " + state.files.length + " 个文件";
    $("queue-section").classList.remove("hidden");
    clearTimeout(state.pollTimer);
    stopFakeProgress();
    setProgress(4, "正在创建上传队列。");

    try {
      var response = await fetch("/api/customer/process", {
        method: "POST",
        body: formData
      });
      var data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "提交失败");
      state.currentBatchId = data.batch_id;
      state.currentJobId = data.job_id;
      state.queueJobs = data.jobs || [];
      state.pollIntervalMs = Math.max(1000, Number(data.poll_interval_seconds || 2) * 1000);
      setProgress(8, "队列已创建，后台最多同时处理 " + (data.max_concurrent_jobs || 2) + " 个文件。");
      renderQueue(data);
      pollBatch(data.batch_id);
    } catch (error) {
      stopFakeProgress();
      setProgress(100, "提交失败：" + error.message);
    }
  }

  function pollBatch(batchId) {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(async function () {
      try {
        var response = await fetch("/api/customer/batches/" + encodeURIComponent(batchId));
        var data = await response.json();
        if (!response.ok) throw new Error(data.detail || "批次读取失败");
        renderQueue(data);
        setProgress(data.progress || 0, batchProgressMessage(data));
        var firstDone = (data.jobs || []).find(function (job) { return job.status === "done"; });
        if (!state.result && firstDone) {
          await loadJobResult(firstDone.file_id);
        }
        if (data.status === "done" || data.status === "partial_failed") {
          await loadHistory();
          return;
        }
        pollBatch(batchId);
      } catch (error) {
        setProgress(state.progress, "队列状态读取失败，正在重试。");
        pollBatch(batchId);
      }
    }, state.pollIntervalMs);
  }

  async function openExistingJob(jobId) {
    showView("process-view");
    setProgress(40, "正在读取历史任务。");
    await loadJobResult(jobId);
  }

  async function loadJobResult(jobId) {
    var response = await fetch("/api/customer/jobs/" + encodeURIComponent(jobId));
    var data = await response.json();
    if (!response.ok || data.status !== "done") {
      setProgress(100, data.error || data.detail || "该任务尚无可展示结果。");
      return;
    }
    state.activeResultJobId = jobId;
    renderResult(data);
  }

  function batchProgressMessage(batch) {
    if (!batch) return "等待队列状态。";
    if (batch.status === "done") return "全部文件处理完成。";
    if (batch.status === "partial_failed") {
      return "批次处理结束，" + batch.failed_count + " 个文件失败。";
    }
    return "处理中：" + batch.done_count + "/" + batch.total_files + " 完成，" +
      batch.processing_count + " 个正在处理，" + batch.queued_count + " 个排队。";
  }

  function jobStatusLabel(status) {
    if (status === "queued" || status === "pending") return "排队中";
    if (status === "processing") return "处理中";
    if (status === "done") return "已完成";
    if (status === "failed") return "失败";
    return status || "未知";
  }

  function renderQueue(batch) {
    var jobs = batch.jobs || [];
    state.queueJobs = jobs;
    $("queue-section").classList.remove("hidden");
    $("queue-total").textContent = (batch.total_files || jobs.length) + " 个文件";
    $("queue-done").textContent = (batch.done_count || 0) + " 个完成";
    $("queue-active").textContent = (batch.processing_count || 0) + " 个处理中";
    $("queue-limit").textContent = "并发上限 " + (batch.max_concurrent_jobs || 2);

    var body = $("queue-body");
    if (!jobs.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-state">暂无队列任务。</td></tr>';
      return;
    }
    body.innerHTML = jobs.map(function (job) {
      var progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
      var canOpen = job.status === "done";
      var activeClass = job.file_id === state.activeResultJobId ? " active-queue-row" : "";
      return (
        '<tr class="queue-row' + activeClass + '">' +
          '<td><div class="queue-file"><strong>' + escapeHtml(job.filename || job.output_filename || job.file_id) + '</strong><span>' + escapeHtml(job.status_message || "") + '</span></div></td>' +
          '<td><span class="job-status job-status-' + escapeHtml(job.status || "unknown") + '">' + escapeHtml(jobStatusLabel(job.status)) + '</span></td>' +
          '<td><div class="queue-progress"><div style="width:' + progress + '%"></div></div><span class="queue-progress-text">' + progress + '%</span></td>' +
          '<td>' + escapeHtml(formatValue(job.line_items_count || 0)) + '</td>' +
          '<td><div class="queue-actions">' +
            '<button class="text-button queue-open" type="button" data-id="' + escapeHtml(job.file_id) + '"' + (canOpen ? "" : " disabled") + '>查看</button>' +
            '<button class="text-button queue-download" type="button" data-id="' + escapeHtml(job.file_id) + '" data-filename="' + escapeHtml(job.output_filename || "customer_result.xlsx") + '"' + (canOpen ? "" : " disabled") + '>Excel</button>' +
            '<button class="text-button queue-download-word" type="button" data-id="' + escapeHtml(job.file_id) + '" data-filename="' + escapeHtml((job.output_filename || "customer_result").replace(/\.xlsx?$/i, "") + ".docx") + '"' + (canOpen ? "" : " disabled") + '>Word</button>' +
          '</div></td>' +
        '</tr>'
      );
    }).join("");
    body.querySelectorAll(".queue-open").forEach(function (button) {
      button.addEventListener("click", function () {
        loadJobResult(button.dataset.id);
      });
    });
    body.querySelectorAll(".queue-download").forEach(function (button) {
      button.addEventListener("click", function () {
        downloadJob(button.dataset.id, button.dataset.filename || "customer_result.xlsx", "excel");
      });
    });
    body.querySelectorAll(".queue-download-word").forEach(function (button) {
      button.addEventListener("click", function () {
        downloadJob(button.dataset.id, button.dataset.filename || "customer_result.docx", "word");
      });
    });
  }

  function getDatabaseParams() {
    var sort = ($("db-sort") && $("db-sort").value || "updated_at:desc").split(":");
    return {
      q: $("db-search") ? $("db-search").value.trim() : "",
      material_special: $("db-material-filter") ? $("db-material-filter").value : "",
      hardness: $("db-hardness-filter") ? $("db-hardness-filter").value : "",
      color: $("db-color-filter") ? $("db-color-filter").value : "",
      has_price: $("db-price-filter") ? $("db-price-filter").value : "",
      sort_by: sort[0] || "updated_at",
      sort_dir: sort[1] || "desc",
      page: state.databasePage,
      size: state.databaseSize
    };
  }

  function buildQuery(params) {
    var search = new URLSearchParams();
    Object.keys(params).forEach(function (key) {
      var value = params[key];
      if (value !== "" && value != null) search.set(key, value);
    });
    return search.toString();
  }

  async function loadProducts() {
    var response = await fetch("/api/customer/products?" + buildQuery(getDatabaseParams()));
    var data = await response.json();
    if (!response.ok) {
      alert(data.detail || "数据库读取失败");
      return;
    }
    state.databaseLoaded = true;
    state.databasePages = data.pages || 1;
    $("db-total").textContent = (data.total || 0) + " 条数据";
    $("db-page-info").textContent = "第 " + (data.page || 1) + " / " + (data.pages || 1) + " 页";
    updateDatabaseFacets(data.facets || {});
    renderProductTable(data.rows || []);
    $("db-prev").disabled = (data.page || 1) <= 1;
    $("db-next").disabled = (data.page || 1) >= (data.pages || 1);
  }

  function updateDatabaseFacets(facets) {
    updateSelectOptions("db-material-filter", facets.materials || []);
    updateSelectOptions("db-hardness-filter", facets.hardnesses || []);
    updateSelectOptions("db-color-filter", facets.colors || []);
  }

  function updateSelectOptions(id, values) {
    var select = $(id);
    if (!select) return;
    var current = select.value;
    select.innerHTML = '<option value="">全部</option>' + values.map(function (value) {
      return '<option value="' + escapeHtml(value) + '">' + escapeHtml(value) + '</option>';
    }).join("");
    if (values.indexOf(current) !== -1) select.value = current;
  }

  function renderProductTable(rows) {
    if (window.Tabulator) {
      $("product-table").classList.remove("hidden");
      $("product-table-fallback").classList.add("hidden");
      if (!state.databaseTable) {
        state.databaseTable = new Tabulator("#product-table", {
          data: rows,
          layout: "fitColumns",
          height: "520px",
          placeholder: "暂无产品数据",
          selectableRows: 1,
          columns: [
            { title: "SKU", field: "sku", minWidth: 150, headerSort: true },
            { title: "物料代码", field: "item_no", minWidth: 130, headerSort: true },
            { title: "品名", field: "product_name", minWidth: 150 },
            { title: "规格型号", field: "part_no", minWidth: 160 },
            { title: "材质", field: "material_special", width: 100 },
            { title: "硬度", field: "hardness", width: 110 },
            { title: "颜色", field: "color", width: 90 },
            {
              title: "最低价",
              field: "lowest_price",
              width: 100,
              hozAlign: "right",
              formatter: function (cell) {
                var value = cell.getValue();
                return value == null ? "<span class='muted-cell'>--</span>" : escapeHtml(formatValue(value));
              }
            },
            { title: "阶梯", field: "price_tiers_count", width: 80, hozAlign: "center" },
            { title: "更新时间", field: "updated_at", minWidth: 150, formatter: dateFormatter }
          ]
        });
        state.databaseTable.on("rowClick", function (_event, row) {
          loadProductDetail(row.getData().id);
        });
      } else {
        state.databaseTable.setData(rows);
      }
      return;
    }
    renderProductFallbackTable(rows);
  }

  function dateFormatter(cell) {
    var value = cell.getValue();
    if (!value) return "";
    return escapeHtml(String(value).replace("T", " ").replace("Z", ""));
  }

  function renderProductFallbackTable(rows) {
    $("product-table").classList.add("hidden");
    var fallback = $("product-table-fallback");
    fallback.classList.remove("hidden");
    if (!rows.length) {
      fallback.innerHTML = '<div class="empty-state">暂无产品数据。</div>';
      return;
    }
    fallback.innerHTML = (
      '<table class="field-table database-fallback-table"><thead><tr>' +
      '<th>SKU</th><th>物料代码</th><th>品名</th><th>材质</th><th>硬度</th><th>颜色</th><th>最低价</th><th>操作</th>' +
      '</tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + escapeHtml(row.sku || "") + '</td>' +
          '<td>' + escapeHtml(row.item_no || "") + '</td>' +
          '<td>' + escapeHtml(row.product_name || "") + '</td>' +
          '<td>' + escapeHtml(row.material_special || "") + '</td>' +
          '<td>' + escapeHtml(row.hardness || "") + '</td>' +
          '<td>' + escapeHtml(row.color || "") + '</td>' +
          '<td>' + escapeHtml(row.lowest_price == null ? "--" : row.lowest_price) + '</td>' +
          '<td><button class="text-button db-detail-fallback" type="button" data-id="' + escapeHtml(row.id) + '">详情</button></td>' +
        '</tr>';
      }).join("") +
      '</tbody></table>'
    );
    fallback.querySelectorAll(".db-detail-fallback").forEach(function (button) {
      button.addEventListener("click", function () { loadProductDetail(button.dataset.id); });
    });
  }

  async function loadProductDetail(productId) {
    var response = await fetch("/api/customer/products/" + encodeURIComponent(productId));
    var data = await response.json();
    if (!response.ok) {
      alert(data.detail || "产品详情读取失败");
      return;
    }
    renderProductDetail(data);
  }

  function renderProductDetail(data) {
    var row = data.row || {};
    var product = data.product || {};
    var logs = data.logs || [];
    var tiers = Array.isArray(product.price_tiers) ? product.price_tiers : [];
    $("product-detail").innerHTML = (
      '<div class="product-detail-head">' +
        '<span>SKU 详情</span>' +
        '<strong>' + escapeHtml(row.sku || row.business_key_value || "--") + '</strong>' +
      '</div>' +
      '<div class="detail-grid">' +
        detailItem("物料代码", row.item_no) +
        detailItem("品名", row.product_name) +
        detailItem("规格型号", row.part_no) +
        detailItem("材质", row.material_special) +
        detailItem("硬度", row.hardness) +
        detailItem("颜色", row.color) +
        detailItem("最低价", row.lowest_price == null ? "--" : row.lowest_price) +
        detailItem("更新时间", row.updated_at ? row.updated_at.replace("T", " ").replace("Z", "") : "") +
      '</div>' +
      '<div class="detail-section"><h3>价格阶梯</h3>' + renderProductTiers(tiers) + '</div>' +
      '<div class="detail-section"><h3>最近操作日志</h3>' + renderProductLogs(logs) + '</div>'
    );
  }

  function detailItem(label, value) {
    return '<div><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(formatValue(value || "--")) + '</strong></div>';
  }

  function renderProductTiers(tiers) {
    if (!tiers.length) return '<div class="empty-state">暂无价格阶梯。</div>';
    return '<div class="detail-list">' + tiers.map(function (tier) {
      return '<div><strong>' + escapeHtml(formatValue(tier.tax_included_price || "--")) + '</strong><span>MOQ ' + escapeHtml(formatValue(tier.moq || "--")) + '</span></div>';
    }).join("") + '</div>';
  }

  function renderProductLogs(logs) {
    if (!logs.length) return '<div class="empty-state">暂无操作日志。</div>';
    return '<div class="detail-list">' + logs.map(function (log) {
      return '<div><strong>' + escapeHtml(log.operation_type || "") + '</strong><span>' + escapeHtml((log.created_at || "").replace("T", " ").replace("Z", "")) + ' · ' + escapeHtml(log.username || "") + '</span></div>';
    }).join("") + '</div>';
  }

  function statusLabel(status) {
    if (status === "ok") return "通过";
    if (status === "missing") return "缺失";
    if (status === "error") return "错误";
    return "空";
  }

  function renderAlerts(result) {
    if (!$("alert-list")) return;
    var alerts = [];
    (result.missing_fields || []).forEach(function (name) {
      alerts.push({ type: "error", text: "缺失必填字段：" + name });
    });
    (result.errors || []).forEach(function (text) {
      alerts.push({ type: "error", text: text });
    });
    if (result.requires_manual_confirmation) {
      alerts.push({ type: "warn", text: "当前业务处理结果需要人工确认。" });
    }
    $("alert-list").innerHTML = alerts.map(function (item) {
      return '<div class="alert-item ' + (item.type === "error" ? "error" : "") + '"><strong>!</strong><span>' + escapeHtml(item.text) + "</span></div>";
    }).join("");
  }

  function renderResult(result) {
    state.result = result;
    state.resultDirty = false;
    showView("result-view");

    $("confirm-button").disabled = !result.requires_manual_confirmation;

    renderAlerts(result);
    renderResultContext(result);
    renderLineItems(result.line_items || []);
  }

  function renderResultContext(result) {
    var context = $("result-context");
    if (!context) return;
    var files = result.source_files || [];
    var source = files.length ? files.join("、") : (result.filename || "");
    var config = result.config_label || result.config_id || "";
    context.innerHTML = [
      source ? "<span>来源：" + escapeHtml(source) + "</span>" : "",
      config ? "<span>配置：" + escapeHtml(config) + "</span>" : "",
      result.page_count ? "<span>页数：" + escapeHtml(result.page_count) + "</span>" : ""
    ].filter(Boolean).join("");
  }

  function renderFieldTable(fields) {
    if (!$("field-table-body")) return;
    $("field-table-body").innerHTML = fields.map(function (field) {
      var status = field.validation_status || "empty";
      var hint = (field.errors || []).join("; ") || field.note || "";
      return (
        '<tr data-field-id="' + escapeHtml(field.id) + '">' +
          '<td class="field-label"><strong>' + escapeHtml(field.label) + "</strong>" +
            "<span>" + escapeHtml(field.id) + (field.required ? " · 必填" : "") + (field.business_key ? " · 主键" : "") + "</span></td>" +
          "<td>" + escapeHtml(formatValue(field.raw_value)) + "</td>" +
          '<td><input class="field-input" data-field-id="' + escapeHtml(field.id) + '" value="' + escapeHtml(formatValue(field.value)) + '" /></td>' +
          '<td><span class="status-badge status-' + escapeHtml(status) + '">' + statusLabel(status) + "</span></td>" +
          '<td class="hint-text">' + escapeHtml(hint || field.source || "") + "</td>" +
        "</tr>"
      );
    }).join("");
  }

  function renderLineItems(items) {
    var section = $("line-items-section");
    var body = $("line-items-body");
    section.classList.remove("hidden");
    $("line-items-count").textContent = items.length + " 个 SKU";
    $("price-row-count").textContent = countVisiblePriceRows(items) + " 条价格";
    $("commit-count").textContent = countCommittedItems(items) + " 条已录入";
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty-state">未识别到 SKU 价格明细。</td></tr>';
      return;
    }
    body.innerHTML = items.map(function (item, index) {
      return renderLineItemRow(item, index) + renderPriceDetailRow(item, index);
    }).join("");
    body.querySelectorAll(".price-toggle").forEach(function (button) {
      button.addEventListener("click", function () {
        if (state.result) state.result.line_items = collectLineItems();
        state.expandedLineItems[button.dataset.lineIndex] = !state.expandedLineItems[button.dataset.lineIndex];
        renderLineItems(state.result ? state.result.line_items || [] : items);
      });
    });
    body.querySelectorAll(".commit-line-item").forEach(function (button) {
      button.addEventListener("click", function () {
        commitLineItem(Number(button.dataset.lineIndex), button);
      });
    });
  }

  function countVisiblePriceRows(items) {
    return (items || []).reduce(function (sum, item) {
      return sum + getVisiblePriceEntries(item).length;
    }, 0);
  }

  function countCommittedItems(items) {
    return (items || []).filter(function (item) {
      return !!item.database_status;
    }).length;
  }

  function renderLineItemRow(item, index) {
    var status = item.validation_status || "ok";
    var errors = (item.errors || []).join("; ");
    var expanded = !!state.expandedLineItems[index];
    return (
      '<tr class="data-row" title="' + escapeHtml(errors) + '">' +
        '<td class="expand-cell">' +
          '<button class="expand-button price-toggle" type="button" data-line-index="' + index + '" aria-label="' + (expanded ? "收起价格明细" : "展开价格明细") + '">' +
            '<span>' + (expanded ? "⌃" : "⌄") + '</span>' +
          '</button>' +
        '</td>' +
        '<td class="line-number-cell">' + escapeHtml(formatValue(item.line_no || index + 1)) + '</td>' +
        '<td>' +
          '<div class="cell-stack">' +
            renderCellInput("SKU", "sku", item.sku || item.internal_sku || item.part_no || item.item_no, index, "sku-input strong-input") +
            renderCellInput("规格", "part_no", item.part_no || item.internal_sku, index, "sku-input") +
          '</div>' +
        '</td>' +
        '<td>' +
          '<div class="cell-stack">' +
            renderCellInput("物料代码", "item_no", item.item_no, index, "compact-input") +
            renderCellInput("品名", "product_name", item.product_name, index, "compact-input") +
          '</div>' +
        '</td>' +
        '<td>' +
          '<div class="attribute-grid">' +
            renderCellInput("材质", "material_special", item.material_special, index, "compact-input") +
            renderCellInput("硬度", "hardness", item.hardness, index, "compact-input") +
            renderCellInput("颜色", "color", item.color, index, "compact-input") +
          '</div>' +
        '</td>' +
        '<td>' + renderPriceSummary(item, index, expanded) + '</td>' +
        '<td>' + renderCellInput("交货", "lead_time", item.lead_time, index, "compact-input") + '</td>' +
        '<td><span class="status-badge status-' + escapeHtml(status) + '">' + statusLabel(status) + "</span></td>" +
        '<td>' + renderCommitControls(item, index) + '</td>' +
      '</tr>'
    );
  }

  function renderCellInput(label, field, value, index, extraClass) {
    return (
      '<label class="cell-field">' +
        '<span>' + escapeHtml(label) + '</span>' +
        '<input class="line-item-input ' + escapeHtml(extraClass || "") + '" data-line-index="' + index + '" data-field="' + escapeHtml(field) + '" value="' + escapeHtml(formatValue(value)) + '" />' +
      '</label>'
    );
  }

  function commitStatusLabel(status) {
    if (status === "created") return "已新增";
    if (status === "price_updated") return "已更新最低价";
    if (status === "price_filled") return "已补价格";
    if (status === "updated_without_price") return "已录入基础信息";
    if (status === "no_update") return "保留原价";
    if (status === "validation_failed") return "录入失败";
    return "";
  }

  function renderCommitControls(item, index) {
    var status = item.database_status || "";
    var message = item.database_message || "";
    var label = status ? "重新录入" : "录入";
    return (
      '<div class="commit-stack">' +
        '<button class="secondary-button commit-line-item" type="button" data-line-index="' + index + '">' + label + '</button>' +
        (status ? '<span class="commit-status">' + escapeHtml(commitStatusLabel(status)) + '</span>' : '') +
        (message ? '<span class="commit-message">' + escapeHtml(message) + '</span>' : '') +
      '</div>'
    );
  }

  function renderPriceSummary(item, lineIndex, expanded) {
    var entries = getVisiblePriceEntries(item);
    var lowest = getLowestPriceEntry(entries);
    var priceText = lowest ? formatPrice(lowest.price.tax_included_price) : "--";
    return (
      '<div class="price-summary-cell">' +
        '<div class="price-main">' +
          '<span>最低含税价</span>' +
          '<strong>' + escapeHtml(priceText) + '</strong>' +
        '</div>' +
        '<button class="text-button price-toggle" type="button" data-line-index="' + lineIndex + '">' +
          entries.length + ' 条价格 · ' + (expanded ? '收起' : '展开') +
        '</button>' +
      '</div>'
    );
  }

  function renderPriceDetailRow(item, lineIndex) {
    var expanded = !!state.expandedLineItems[lineIndex];
    var entries = getVisiblePriceEntries(item);
    return (
      '<tr class="price-detail-row' + (expanded ? '' : ' hidden') + '">' +
        '<td colspan="9">' +
          '<div class="price-detail-panel">' +
            '<div class="price-detail-head">' +
              '<strong>价格明细</strong>' +
              '<span>录入时自动选择最低含税单价</span>' +
            '</div>' +
            '<div class="price-tier-table">' +
              '<div class="price-tier-row price-tier-head">' +
                '<span>类型</span><span>含税单价</span><span>MOQ</span><span>数量</span><span>单位</span><span>价税合计</span><span>来源</span>' +
              '</div>' +
              entries.map(function (entry, visibleIndex) {
                return renderPriceTierRow(entry.price, lineIndex, entry.sourceIndex, visibleIndex);
              }).join("") +
            '</div>' +
          '</div>' +
        '</td>' +
      '</tr>'
    );
  }

  function renderPriceTierRow(price, lineIndex, priceIndex, visibleIndex) {
    var label = price.price_type || ("价格 " + (visibleIndex + 1));
    var source = price.source_table || price.note || "";
    return (
      '<div class="price-tier-row">' +
        '<span class="tier-label">' + escapeHtml(label) + '</span>' +
        renderPriceInput(lineIndex, priceIndex, "tax_included_price", price.tax_included_price, "price-input") +
        renderPriceInput(lineIndex, priceIndex, "moq", price.moq, "qty-input") +
        renderPriceInput(lineIndex, priceIndex, "quantity", price.quantity, "qty-input") +
        '<span>' + escapeHtml(formatValue(price.unit)) + '</span>' +
        '<span>' + escapeHtml(formatValue(price.amount)) + '</span>' +
        '<span class="tier-source">' + escapeHtml(source) + '</span>' +
      '</div>'
    );
  }

  function renderPriceInput(lineIndex, priceIndex, field, value, extraClass) {
    return (
      '<input class="line-price-input ' + escapeHtml(extraClass || "") + '" data-line-index="' + lineIndex + '" data-price-index="' + priceIndex + '" data-field="' + escapeHtml(field) + '" value="' + escapeHtml(formatValue(value)) + '" />'
    );
  }

  function parsePriceNumber(value) {
    var text = String(value == null ? "" : value).replace(/[¥￥,]/g, "").trim();
    if (!text) return null;
    var parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatPrice(value) {
    var parsed = parsePriceNumber(value);
    if (parsed == null) return formatValue(value);
    return String(parsed);
  }

  function getLowestPriceEntry(entries) {
    var candidates = entries
      .map(function (entry) {
        return { entry: entry, value: parsePriceNumber(entry.price && entry.price.tax_included_price) };
      })
      .filter(function (item) {
        return item.value != null;
      });
    if (!candidates.length) return null;
    candidates.sort(function (a, b) { return a.value - b.value; });
    return candidates[0].entry;
  }

  function getRawPrices(item) {
    return Array.isArray(item.prices) && item.prices.length
      ? item.prices
      : [{
          tax_included_price: item.tax_included_price,
          amount: item.amount,
          quantity: item.quantity,
          unit: item.unit,
          moq: item.moq || "",
          price_type: "含税单价"
        }];
  }

  function shouldHidePriceRow(price) {
    var label = [
      price && price.price_type,
      price && price.source_table,
      price && price.note
    ].filter(Boolean).join(" ");
    return hiddenPriceRowTerms.some(function (term) {
      return label.indexOf(term) !== -1;
    });
  }

  function filterPriceRows(prices) {
    if (!Array.isArray(prices) || prices.length <= 1) return prices || [];
    var filtered = prices.filter(function (price) {
      return !shouldHidePriceRow(price || {});
    });
    return filtered.length ? filtered : prices;
  }

  function getVisiblePriceEntries(item) {
    var entries = getRawPrices(item).map(function (price, sourceIndex) {
      return { price: price || {}, sourceIndex: sourceIndex };
    });
    if (entries.length <= 1) return entries;
    var filtered = entries.filter(function (entry) {
      return !shouldHidePriceRow(entry.price);
    });
    return filtered.length ? filtered : entries;
  }

  function collectFields() {
    return Array.from(document.querySelectorAll(".field-input")).map(function (input) {
      return { id: input.dataset.fieldId, value: input.value };
    });
  }

  function cloneLineItem(item) {
    return JSON.parse(JSON.stringify(item || {}));
  }

  function collectLineItems() {
    var items = (state.result && state.result.line_items ? state.result.line_items : []).map(cloneLineItem);
    document.querySelectorAll(".line-item-input").forEach(function (input) {
      var lineIndex = Number(input.dataset.lineIndex);
      var field = input.dataset.field;
      if (!items[lineIndex] || !field) return;
      items[lineIndex][field] = input.value;
    });
    document.querySelectorAll(".line-price-input").forEach(function (input) {
      var lineIndex = Number(input.dataset.lineIndex);
      var priceIndex = Number(input.dataset.priceIndex);
      var field = input.dataset.field;
      if (!items[lineIndex] || !field) return;
      if (!Array.isArray(items[lineIndex].prices)) items[lineIndex].prices = [];
      if (!items[lineIndex].prices[priceIndex]) items[lineIndex].prices[priceIndex] = {};
      items[lineIndex].prices[priceIndex][field] = input.value;
    });
    items.forEach(function (item) {
      if (Array.isArray(item.prices)) item.prices = filterPriceRows(item.prices);
    });
    return items;
  }

  async function commitLineItem(lineIndex, button) {
    if (!state.result) return;
    var items = collectLineItems();
    var lineItem = items[lineIndex];
    if (!lineItem) return;
    var originalText = button.textContent;
    button.disabled = true;
    button.textContent = "录入中...";
    try {
      var response = await fetch("/api/customer/line-items/commit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: state.result.file_id || state.result.job_id,
          config_id: state.result.config_id,
          line_index: lineIndex,
          line_item: lineItem
        })
      });
      var data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "录入失败");
      state.result.line_items = items;
      state.resultDirty = true;
      state.result.line_items[lineIndex] = data.line_item || lineItem;
      renderLineItems(state.result.line_items);
    } catch (error) {
      alert(error.message);
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  async function regenerateResult(format) {
    if (!state.result || state.regenerating) return;
    state.regenerating = true;
    $("result-view").inert = true;
    $("result-view").setAttribute("aria-busy", "true");
    format = format === "word" ? "word" : "excel";
    $("download-button").disabled = true;
    if ($("download-word-button")) $("download-word-button").disabled = true;
    $("regenerate-button").disabled = true;
    $("regenerate-button").textContent = "处理中...";
    try {
      var response = await fetch("/api/customer/regenerate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: state.result.file_id || state.result.job_id,
          config_id: state.result.config_id,
          fields: collectFields(),
          line_items: collectLineItems()
        })
      });
      var data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "重新生成失败");
      renderResult(data);
      if (format === "word") await downloadWordFile(); else await downloadFile();
    } catch (error) {
      alert(error.message);
    } finally {
      state.regenerating = false;
      $("result-view").inert = false;
      $("result-view").removeAttribute("aria-busy");
      $("download-button").disabled = false;
      if ($("download-word-button")) $("download-word-button").disabled = false;
      $("regenerate-button").disabled = false;
      $("regenerate-button").textContent = "重新校验并导出";
    }
  }

  async function confirmResult() {
    if (!state.result || !state.result.requires_manual_confirmation) return;
    if (state.resultDirty) {
      alert("字段已修改，请先重新校验并导出，再进行人工确认。");
      return;
    }
    var note = window.prompt("请输入人工确认备注（可留空）：", "");
    if (note === null) return;
    try {
      var response = await fetch("/api/customer/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: state.result.file_id || state.result.job_id,
          decision: "confirmed",
          note: note || ""
        })
      });
      var data = await response.json();
      if (!response.ok) throw new Error(data.detail || "人工确认失败");
      state.result.business_result = data.business_result;
      state.result.requires_manual_confirmation = false;
      renderResult(state.result);
    } catch (error) {
      alert(error.message);
    }
  }

  async function downloadFile() {
    if (!state.result) return;
    if (state.resultDirty) return regenerateResult("excel");
    return downloadJob(state.result.file_id || state.result.job_id, state.result.filename || "customer_result.xlsx", "excel");
  }

  async function downloadWordFile() {
    if (!state.result) return;
    if (state.resultDirty) return regenerateResult("word");
    var baseName = (state.result.filename || "customer_result").replace(/\.xlsx?$/i, "");
    return downloadJob(state.result.file_id || state.result.job_id, baseName + ".docx", "word");
  }

  async function downloadJob(fileId, filename, format) {
    if (!fileId) return;
    format = format || "excel";
    var baseUrl = "/api/customer/download/" + encodeURIComponent(fileId);
    var url = format === "word" ? baseUrl + "/word?filename=" + encodeURIComponent(filename)
                               : baseUrl + "?filename=" + encodeURIComponent(filename);
    try {
      var token = window.Auth.getToken();
      var response = await fetch(url, {
        headers: token ? { Authorization: "Bearer " + token } : {}
      });
      if (!response.ok) throw new Error("下载失败：" + response.status);
      var blob = await response.blob();
      var blobUrl = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (error) {
      alert(error.message);
    }
  }

  function resetTask() {
    clearTimeout(state.pollTimer);
    stopFakeProgress();
    state.files = [];
    state.resultDirty = false;
    state.result = null;
    state.expandedLineItems = {};
    state.queueJobs = [];
    state.currentBatchId = "";
    state.activeResultJobId = "";
    state.currentJobId = "";
    $("queue-section").classList.add("hidden");
    renderFiles();
    showView("upload-view");
  }

  function bindEvents() {
    $("result-view").addEventListener("input", function (event) {
      if (event.target.matches(".field-input, .line-item-input, .line-price-input")) {
        state.resultDirty = true;
      }
    });
    var dropZone = $("drop-zone");
    var input = $("file-input");

    dropZone.addEventListener("click", function () { input.click(); });
    dropZone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") input.click();
    });
    input.addEventListener("change", function () {
      addFiles(input.files);
      input.value = "";
    });

    ["dragenter", "dragover"].forEach(function (name) {
      dropZone.addEventListener(name, function (event) {
        event.preventDefault();
        dropZone.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      dropZone.addEventListener(name, function (event) {
        event.preventDefault();
        dropZone.classList.remove("dragover");
      });
    });
    dropZone.addEventListener("drop", function (event) {
      addFiles(event.dataTransfer.files);
    });

    $("config-select").addEventListener("change", function () {
      state.selectedConfig = $("config-select").value;
      renderConfigDescription();
      renderFiles();
    });
    $("start-button").addEventListener("click", startProcessing);
    $("refresh-history").addEventListener("click", loadHistory);
    $("tab-workbench").addEventListener("click", openWorkbench);
    $("tab-database").addEventListener("click", openDatabaseView);
    $("db-refresh").addEventListener("click", loadProducts);
    $("db-apply-filter").addEventListener("click", function () {
      state.databasePage = 1;
      loadProducts();
    });
    $("db-search").addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        state.databasePage = 1;
        loadProducts();
      }
    });
    ["db-material-filter", "db-hardness-filter", "db-color-filter", "db-price-filter", "db-sort"].forEach(function (id) {
      $(id).addEventListener("change", function () {
        state.databasePage = 1;
        loadProducts();
      });
    });
    $("db-page-size").addEventListener("change", function () {
      state.databaseSize = Number($("db-page-size").value || 20);
      state.databasePage = 1;
      loadProducts();
    });
    $("db-prev").addEventListener("click", function () {
      if (state.databasePage > 1) {
        state.databasePage -= 1;
        loadProducts();
      }
    });
    $("db-next").addEventListener("click", function () {
      if (state.databasePage < state.databasePages) {
        state.databasePage += 1;
        loadProducts();
      }
    });
    $("logout-button").addEventListener("click", function () { window.Auth.logout(); });
    $("admin-link").addEventListener("click", function () { window.location.href = "/admin"; });
    $("new-task-button").addEventListener("click", resetTask);
    $("regenerate-button").addEventListener("click", regenerateResult);
    $("download-button").addEventListener("click", downloadFile);
    $("download-word-button").addEventListener("click", downloadWordFile);
    $("confirm-button").addEventListener("click", confirmResult);
  }

  async function init() {
    window.Auth.requireAuth();
    bindEvents();
    await loadProfile();
    await loadConfigs();
    await loadHistory();
    renderFiles();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
