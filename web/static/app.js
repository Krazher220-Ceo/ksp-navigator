/*
  web/static/app.js — Mini App, блок Б10. Vanilla JS, без сборки.

  Два экрана, переключение — по query-параметру ?preview=<id> в URL:
  есть параметр -> экран «Предпросмотр», нет -> экран «Шаблоны».
  Путь /preview/{id} тут не годится: web/api.py (блок Б9) раздаёт
  index.html только на корне через StaticFiles(html=True) — на любой
  другой путь без физического файла был бы 404. Query-параметр не
  требует правок бэкенда: index.html отдаётся всегда с корня, а какой
  экран показать — решает уже этот файл.

  initData уходит в заголовке X-Telegram-Init-Data на КАЖДОМ запросе
  к /api/* (Б10.3) — без него web/auth.py (блок Б9) отвечает 401.
*/

(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;

  // --- Б10.3: тема Telegram -> CSS-переменные, ничего не хардкодим ---

  function applyTheme() {
    if (!tg || !tg.themeParams) return;
    const theme = tg.themeParams;
    const root = document.documentElement.style;
    const map = {
      "--tg-bg": theme.bg_color,
      "--tg-text": theme.text_color,
      "--tg-hint": theme.hint_color,
      "--tg-link": theme.link_color,
      "--tg-button": theme.button_color,
      "--tg-button-text": theme.button_text_color,
      "--tg-secondary-bg": theme.secondary_bg_color,
      "--tg-section-separator": theme.section_separator_color,
    };
    for (const key in map) {
      if (map[key]) root.setProperty(key, map[key]);
    }
    if (theme.bg_color && typeof tg.setBackgroundColor === "function") {
      try { tg.setBackgroundColor(theme.bg_color); } catch (e) { /* старый клиент — не критично */ }
    }
    if (theme.bg_color && typeof tg.setHeaderColor === "function") {
      try { tg.setHeaderColor(theme.bg_color); } catch (e) { /* старый клиент — не критично */ }
    }
  }

  function initTelegram() {
    if (!tg) return; // открыли не в Telegram (например, для отладки в браузере) — не падаем
    tg.ready();
    tg.expand();
    applyTheme();
    tg.onEvent("themeChanged", applyTheme);
  }

  // --- общий помощник запросов к /api/* с initData в заголовке ---

  async function apiFetch(path, options) {
    const headers = {};
    if (tg && tg.initData) {
      headers["X-Telegram-Init-Data"] = tg.initData;
    }
    const requestOptions = Object.assign({}, options || {});
    requestOptions.headers = Object.assign({}, requestOptions.headers || {}, headers);
    const response = await fetch(path, requestOptions);
    if (!response.ok) {
      const err = new Error("Запрос не удался: " + response.status);
      err.status = response.status;
      throw err;
    }
    return response;
  }

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  // =====================================================================
  // Экран «Шаблоны» (Б10.1) — только выбор, ничего не редактирует
  // =====================================================================

  let selectedTemplateId = null;
  let selectedTemplateName = "";

  const TEMPLATE_FIELD_LABELS = {
    organizaciya: "Организация образования",
    razdel: "Раздел",
    fio_pedagoga: "ФИО педагога",
    data: "Дата",
    klass: "Класс",
    prisutstvuet: "Присутствовали",
    otsutstvuet: "Отсутствовали",
    chasy: "Количество часов",
    tema_uroka: "Тема урока",
    celi_obucheniya: "Цели обучения",
    celi_uroka: "Цели урока",
    adaptaciya_oop: "Адаптация для ООП",
  };

  const TEMPLATE_BLOCK_LABELS = {
    shapka: "Шапка документа",
    tema: "Тема урока",
    celi: "Цели",
    hod_uroka: "Ход урока",
    primechanie: "Примечание",
  };

  const TEMPLATE_COLUMN_LABELS = {
    etap_vremya: "Этап и время",
    deystviya_pedagoga: "Действия педагога",
    deystviya_uchenika: "Действия ученика",
    resursy: "Ресурсы",
    ocenivanie: "Оценивание",
    domashnee_zadanie: "Домашнее задание",
    dop_literatura: "Дополнительная литература",
  };

  function labelForTemplateField(key) {
    return TEMPLATE_FIELD_LABELS[key] || key;
  }

  function labelForTemplateColumn(key) {
    return TEMPLATE_COLUMN_LABELS[key] || key;
  }

  function showTemplateFormPreview(template) {
    const previewEl = document.getElementById("template-form-preview");
    const titleEl = document.getElementById("template-form-preview-title");
    const blocksEl = document.getElementById("template-form-preview-blocks");
    const structure = template.structure_json || {};
    const blocks = Array.isArray(structure.blocks) ? structure.blocks : [];

    titleEl.textContent = "Форма: " + (template.name || "шаблон");
    blocksEl.innerHTML = blocks.map(function (block) {
      const fields = Array.isArray(block.fields) ? block.fields : [];
      const columns = Array.isArray(block.columns) ? block.columns : [];
      const items = fields.map(function (key) {
        return "<li>" + escapeHtml(labelForTemplateField(key)) + "</li>";
      }).join("");
      const table = columns.length
        ? '<div class="table-scroll"><table class="template-form-table"><thead><tr>' +
          columns.map(function (key) {
            return "<th>" + escapeHtml(labelForTemplateColumn(key)) + "</th>";
          }).join("") +
          "</tr></thead><tbody><tr>" + columns.map(function () {
            return "<td> </td>";
          }).join("") + "</tr></tbody></table></div>"
        : "";
      return (
        '<section class="template-form-block">' +
          "<h3>" + escapeHtml(TEMPLATE_BLOCK_LABELS[block.key] || block.key) + "</h3>" +
          (items ? '<ul class="template-form-fields">' + items + "</ul>" : "") +
          table +
        "</section>"
      );
    }).join("") || '<div class="state-message">Структура этого шаблона пока не определена.</div>';
    previewEl.hidden = false;
    previewEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function hideTemplateFormPreview() {
    document.getElementById("template-form-preview").hidden = true;
  }

  function renderTemplates(templates) {
    const listEl = document.getElementById("templates-list");
    const stateEl = document.getElementById("templates-state");
    const uploadButton = document.getElementById("upload-own-button");

    if (!templates.length) {
      stateEl.textContent = "Шаблонов пока нет.";
      return;
    }
    stateEl.hidden = true;
    uploadButton.hidden = false;

    listEl.innerHTML = templates.map(function (tpl, index) {
      const badge = tpl.is_official
        ? '<span class="badge-official">официальный</span>'
        : "";
      const description = tpl.description
        ? '<div class="template-card-description">' + escapeHtml(tpl.description) + "</div>"
        : "";
      return (
        '<div class="template-card-wrap">' +
        '<button type="button" class="template-card" data-id="' + tpl.id + '">' +
          '<div class="template-card-title-row">' +
            '<span class="template-card-name">' + escapeHtml(tpl.name) + "</span>" +
            badge +
          "</div>" +
          '<div class="template-card-source">' + escapeHtml(tpl.source || "") + "</div>" +
          description +
        "</button>" +
        '<button type="button" class="template-preview-button" data-template-index="' + index + '">Посмотреть</button>' +
        "</div>"
      );
    }).join("");

    listEl.querySelectorAll(".template-card").forEach(function (card) {
      card.addEventListener("click", function () {
        selectedTemplateId = card.getAttribute("data-id");
        selectedTemplateName = card.querySelector(".template-card-name").textContent;
        listEl.querySelectorAll(".template-card").forEach(function (c) {
          c.classList.toggle("selected", c === card);
        });
        updateTemplatesMainButton();
      });
    });

    listEl.querySelectorAll(".template-preview-button").forEach(function (button) {
      button.addEventListener("click", function () {
        const template = templates[Number(button.getAttribute("data-template-index"))];
        if (template) showTemplateFormPreview(template);
      });
    });
  }

  function updateTemplatesMainButton() {
    if (!tg) return;
    if (!selectedTemplateId) {
      tg.MainButton.hide();
      return;
    }
    tg.MainButton.setText("Использовать этот шаблон");
    tg.MainButton.show();
  }

  async function sendSelectedTemplate() {
    if (!tg || !selectedTemplateId) return;
    tg.MainButton.showProgress();
    try {
      // Серверный fallback обязателен: sendData работает у reply-кнопки,
      // но молчит при открытии синей кнопкой меню чата. Сначала сохраняем
      // одноразовый выбор с TTL, затем пробуем обычную доставку в бот.
      await apiFetch("/api/template-selection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: Number(selectedTemplateId) }),
      });

      const closeAndDeliver = function () {
        tg.sendData(JSON.stringify({ template_id: selectedTemplateId }));
        // В контексте меню sendData ничего не делает — закрываем сами.
        window.setTimeout(function () { tg.close(); }, 250);
      };
      const confirmation = "Шаблон выбран: " + selectedTemplateName +
        ". Если бот не ответит сразу, отправьте /generate.";
      if (typeof tg.showAlert === "function") {
        tg.showAlert(confirmation, closeAndDeliver);
      } else {
        closeAndDeliver();
      }
    } catch (e) {
      if (typeof tg.showAlert === "function") {
        tg.showAlert("Не удалось сохранить выбор. Попробуйте ещё раз.");
      }
    } finally {
      tg.MainButton.hideProgress();
    }
  }

  function initTemplatesScreen() {
    document.getElementById("screen-templates").classList.add("active");

    if (tg) {
      tg.MainButton.onClick(sendSelectedTemplate);
    }

    // Загрузка файла — в боте, не здесь (Б10.1: кнопка закрывает Mini App
    // и подсказывает команду). Mini App файлы не принимает и не должен.
    document.getElementById("upload-own-button").addEventListener("click", function () {
      if (!tg) return;
      // Приём файла не переносим в Mini App: это только сигнал боту
      // открыть уже существующий диалог загрузки. После него учитель
      // просто прикладывает .doc/.docx, не вводя команду вручную.
      tg.sendData(JSON.stringify({ action: "upload_template" }));
      window.setTimeout(function () { tg.close(); }, 250);
    });

    document.getElementById("template-form-preview-close").addEventListener("click", hideTemplateFormPreview);

    apiFetch("/api/templates")
      .then(function (r) { return r.json(); })
      .then(renderTemplates)
      .catch(function () {
        document.getElementById("templates-state").textContent =
          "Не удалось загрузить шаблоны. Попробуйте открыть заново из бота.";
      });
  }

  // =====================================================================
  // Экран «Предпросмотр» (Б10.2) — только просмотр и скачивание
  // =====================================================================

  const HOD_UROKA_COLUMNS = [
    { key: "etap", label: "Этап" },
    { key: "vremya", label: "Время" },
    { key: "deystviya_pedagoga", label: "Действия педагога" },
    { key: "deystviya_uchenika", label: "Действия ученика" },
    { key: "resursy", label: "Ресурсы" },
    { key: "ocenivanie", label: "Оценивание" },
  ];

  const FIELD_LABELS = [
    { key: "razdel", label: "Раздел" },
    { key: "celi_obucheniya", label: "Цели обучения" },
    { key: "celi_uroka", label: "Цели урока" },
  ];

  function fieldToText(value) {
    if (Array.isArray(value)) return value.join("\n");
    return value == null ? "" : String(value);
  }

  function renderPreview(content, ksbId) {
    document.getElementById("preview-state").hidden = true;
    document.getElementById("preview-content").hidden = false;

    document.getElementById("preview-topic").textContent = content.tema_uroka || "Без темы";

    const fieldsEl = document.getElementById("preview-fields");
    fieldsEl.innerHTML = FIELD_LABELS.map(function (f) {
      const value = fieldToText(content[f.key]);
      if (!value) return "";
      return (
        '<div class="field-row">' +
          '<div class="field-label">' + escapeHtml(f.label) + "</div>" +
          '<div class="field-value">' + escapeHtml(value) + "</div>" +
        "</div>"
      );
    }).join("");

    const headEl = document.getElementById("preview-table-head");
    headEl.innerHTML = "<tr>" + HOD_UROKA_COLUMNS.map(function (c) {
      return "<th>" + escapeHtml(c.label) + "</th>";
    }).join("") + "</tr>";

    const rows = Array.isArray(content.hod_uroka) ? content.hod_uroka : [];
    const bodyEl = document.getElementById("preview-table-body");
    bodyEl.innerHTML = rows.map(function (row) {
      return "<tr>" + HOD_UROKA_COLUMNS.map(function (c) {
        const mobileClass = c.key === "etap" ? " preview-stage-cell" :
          (c.key === "vremya" ? " preview-time-cell" : "");
        return '<td class="preview-cell' + mobileClass + '" data-label="' + escapeHtml(c.label) + '">' +
          escapeHtml(row[c.key]) + "</td>";
      }).join("") + "</tr>";
    }).join("");

    renderRazdatochnyeMaterialy(content);

    if (tg) {
      tg.MainButton.setText("Скачать .docx");
      tg.MainButton.show();
      tg.MainButton.onClick(function () { downloadDocx(ksbId); });
    }
  }

  // Р3.3: раздаточные материалы — необязательный блок, есть только если
  // учитель их запросил при генерации (Р3.1). Только просмотр — как и
  // весь экран «Предпросмотр» (MASTER.md, п.7.1 и п.11): нет ни одного
  // элемента ввода, только текст из content.
  const MATERIALY_CARD_FIELDS = [
    { key: "zadanie", label: "Задание" },
    { key: "podskazka", label: "Подсказка" },
    { key: "reshenie", label: "Решение" },
  ];

  function renderRazdatochnyeMaterialy(content) {
    const cardsSection = document.getElementById("preview-cards-section");
    const kriteriiSection = document.getElementById("preview-kriterii-section");

    const cards = Array.isArray(content.razdatochnye_materialy) ? content.razdatochnye_materialy : [];
    const kriterii = Array.isArray(content.kriterii_uspeha) ? content.kriterii_uspeha : [];

    if (cards.length) {
      document.getElementById("preview-cards-list").innerHTML = cards.map(function (card) {
        const titleBits = [card.uroven, card.metka ? "(" + card.metka + ")" : ""]
          .filter(Boolean).map(escapeHtml).join(" ");
        const fields = MATERIALY_CARD_FIELDS.map(function (f) {
          const value = card[f.key];
          if (!value) return ""; // решения может не быть у творческого задания — не выдумываем
          return (
            '<div class="materialy-card-field">' +
              '<span class="materialy-card-field-label">' + escapeHtml(f.label) + ':</span> ' +
              escapeHtml(value) +
            "</div>"
          );
        }).join("");
        return (
          '<div class="materialy-card">' +
            (titleBits ? '<div class="materialy-card-title">' + titleBits + "</div>" : "") +
            fields +
          "</div>"
        );
      }).join("");
      cardsSection.hidden = false;
    } else {
      cardsSection.hidden = true;
    }

    if (kriterii.length) {
      document.getElementById("preview-kriterii-list").innerHTML = kriterii
        .map(function (item) { return "<li>" + escapeHtml(item) + "</li>"; })
        .join("");
      kriteriiSection.hidden = false;
    } else {
      kriteriiSection.hidden = true;
    }
  }

  async function downloadDocx(ksbId) {
    if (tg) tg.MainButton.showProgress();
    try {
      const response = await apiFetch("/api/download/" + encodeURIComponent(ksbId));
      const blob = await response.blob();

      let filename = "ksp.docx";
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      if (match) filename = decodeURIComponent(match[1]);

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (e) {
      if (tg && typeof tg.showAlert === "function") {
        tg.showAlert("Не удалось скачать файл. Попробуйте ещё раз.");
      }
    } finally {
      if (tg) tg.MainButton.hideProgress();
    }
  }

  function initPreviewScreen(ksbId) {
    document.getElementById("screen-preview").classList.add("active");

    apiFetch("/api/preview/" + encodeURIComponent(ksbId))
      .then(function (r) { return r.json(); })
      .then(function (content) { renderPreview(content, ksbId); })
      .catch(function (e) {
        const text = e.status === 404
          ? "Этот КСП не найден — возможно, файл уже удалён."
          : "Не удалось загрузить предпросмотр. Попробуйте открыть заново из бота.";
        document.getElementById("preview-state").textContent = text;
      });
  }

  // =====================================================================
  // Экран «Дашборд» (М5.3) — только показ, тот же /api/dashboard, что
  // бот показывает текстом (core.dashboard.collect — единый расчёт,
  // М5.1). Никаких графиков библиотеками — простые CSS div-полоски.
  // =====================================================================

  function renderDashboard(data) {
    document.getElementById("dashboard-state").hidden = true;
    document.getElementById("dashboard-content").hidden = false;

    const noProfileEl = document.getElementById("dashboard-no-profile");
    const bodyEl = document.getElementById("dashboard-body");

    if (!data.has_profile) {
      noProfileEl.hidden = false;
      noProfileEl.textContent =
        "Дашборд станет полезным, когда заведёте профиль в боте: /teacher.";
      bodyEl.hidden = true;
      renderUptime(data.uptime);
      return;
    }

    noProfileEl.hidden = true;
    bodyEl.hidden = false;

    document.getElementById("dash-pending").textContent = data.queue.pending;
    document.getElementById("dash-processing").textContent = data.queue.processing;
    document.getElementById("dash-failed-7d").textContent = data.queue.failed_7d;

    document.getElementById("dash-ksp-usage").textContent =
      data.usage_today.generate_ksp + " из " + data.usage_today.generate_ksp_limit;
    document.getElementById("dash-ktp-usage").textContent =
      data.usage_today.generate_ktp + " из " + data.usage_today.generate_ktp_limit;

    document.getElementById("dash-ksp-total").textContent = data.generated_ksp.total;
    document.getElementById("dash-ksp-7d").textContent = data.generated_ksp.last_7d;
    document.getElementById("dash-ksp-30d").textContent = data.generated_ksp.last_30d;

    const covered = data.ktp_coverage.covered;
    const notCovered = data.ktp_coverage.not_covered;
    const coverageTotal = covered + notCovered;
    const coveragePct = coverageTotal ? Math.round((covered / coverageTotal) * 100) : 0;
    document.getElementById("dash-coverage-bar").innerHTML =
      '<div class="dash-bar-fill" style="width:' + coveragePct + '%"></div>';
    document.getElementById("dash-covered").textContent = covered;
    document.getElementById("dash-not-covered").textContent = notCovered;

    const unparsedEl = document.getElementById("dash-unparsed-note");
    if (data.unparsed_planned_dates) {
      unparsedEl.hidden = false;
      unparsedEl.textContent = "Дата не распознана: " + data.unparsed_planned_dates + " уроков";
    } else {
      unparsedEl.hidden = true;
    }

    const upcomingEl = document.getElementById("dash-upcoming-list");
    const upcoming = Array.isArray(data.upcoming_lessons_without_ksp) ? data.upcoming_lessons_without_ksp : [];
    if (upcoming.length) {
      upcomingEl.innerHTML = upcoming.map(function (lesson) {
        return (
          '<div class="dash-stat-row">' +
            '<span>' + escapeHtml(lesson.planned_date) + '</span>' +
            '<span>' + escapeHtml(lesson.topic) + '</span>' +
          '</div>'
        );
      }).join("");
    } else {
      upcomingEl.innerHTML = '<div class="state-message">Ближайших уроков без КСП не видно.</div>';
    }

    const styleEl = document.getElementById("dash-style-profile");
    if (data.style_profile.exists) {
      styleEl.textContent = "Есть, собран из " + data.style_profile.samples_count + " файлов.";
    } else {
      styleEl.textContent = "Нет — /upload_ksp в боте, чтобы КСП собирались в вашей манере.";
    }

    renderUptime(data.uptime);
  }

  // М7.4: живучесть — та же таблица reason -> человеческий текст, что и
  // bot/texts.py (DASHBOARD_REASON_LABELS). Дублируется, а не запрашивается
  // с сервера — четыре строки, не стоит отдельного API-эндпоинта.
  const REASON_LABELS = {
    dns_fail: "не резолвился DNS",
    tcp_fail: "сеть недоступна (TCP)",
    telegram_5xx: "Telegram отвечает ошибкой сервера",
    bot_process: "процесс бота не запущен",
    worker_stuck: "воркер очереди завис",
  };

  function formatDowntime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const remMinutes = minutes % 60;
    return hours ? hours + " ч " + remMinutes + " мин" : remMinutes + " мин";
  }

  function renderUptime(uptime) {
    document.getElementById("dash-uptime-title").textContent = "Живучесть (с " + uptime.measured_since + ")";

    const lastEl = document.getElementById("dash-uptime-last-incident");
    if (uptime.last_incident) {
      const reasonLabel = REASON_LABELS[uptime.last_incident.reason] || uptime.last_incident.reason;
      lastEl.hidden = false;
      if (!uptime.last_incident.ended_at) {
        lastEl.textContent = "Сейчас недоступно: " + reasonLabel;
      } else {
        lastEl.textContent =
          "Последний сбой: " + uptime.last_incident.started_at.slice(0, 16).replace("T", " ") +
          " — " + uptime.last_incident.ended_at.slice(0, 16).replace("T", " ") +
          ", причина: " + reasonLabel;
      }
    } else {
      lastEl.hidden = true;
    }

    const statsEl = document.getElementById("dash-uptime-stats");
    statsEl.textContent = uptime.incidents_7d
      ? "Сбоев за 7 дней: " + uptime.incidents_7d + ", суммарно недоступно: " + formatDowntime(uptime.downtime_seconds_7d)
      : "Сбоев за 7 дней не было";
  }

  function setDashboardUpdatedAt() {
    const now = new Date();
    document.getElementById("dashboard-updated-at").textContent =
      "данные на " + now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }

  function loadDashboard() {
    const refreshButton = document.getElementById("dashboard-refresh");
    refreshButton.disabled = true;
    document.getElementById("screen-dashboard").classList.add("active");
    apiFetch("/api/dashboard")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderDashboard(data);
        setDashboardUpdatedAt();
      })
      .catch(function () {
        document.getElementById("dashboard-state").textContent =
          "Не удалось загрузить дашборд. Попробуйте открыть заново из бота.";
      })
      .finally(function () {
        refreshButton.disabled = false;
      });
  }

  function initDashboardScreen() {
    document.getElementById("dashboard-refresh").addEventListener("click", loadDashboard);
    loadDashboard();
  }

  // =====================================================================
  // Точка входа: ?preview=<id> -> предпросмотр, ?dashboard=1 -> дашборд,
  // иначе шаблоны (М5.3: третий экран добавлен тем же способом
  // маршрутизации через query-параметр, что и предпросмотр)
  // =====================================================================

  function main() {
    initTelegram();
    const params = new URLSearchParams(window.location.search);
    const previewId = params.get("preview");
    if (previewId) {
      initPreviewScreen(previewId);
    } else if (params.get("dashboard")) {
      initDashboardScreen();
    } else {
      initTemplatesScreen();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
