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

  async function apiFetch(path) {
    const headers = {};
    if (tg && tg.initData) {
      headers["X-Telegram-Init-Data"] = tg.initData;
    }
    const response = await fetch(path, { headers });
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

    listEl.innerHTML = templates.map(function (tpl) {
      const badge = tpl.is_official
        ? '<span class="badge-official">официальный</span>'
        : "";
      const description = tpl.description
        ? '<div class="template-card-description">' + escapeHtml(tpl.description) + "</div>"
        : "";
      return (
        '<button type="button" class="template-card" data-id="' + tpl.id + '">' +
          '<div class="template-card-title-row">' +
            '<span class="template-card-name">' + escapeHtml(tpl.name) + "</span>" +
            badge +
          "</div>" +
          '<div class="template-card-source">' + escapeHtml(tpl.source || "") + "</div>" +
          description +
        "</button>"
      );
    }).join("");

    listEl.querySelectorAll(".template-card").forEach(function (card) {
      card.addEventListener("click", function () {
        selectedTemplateId = card.getAttribute("data-id");
        listEl.querySelectorAll(".template-card").forEach(function (c) {
          c.classList.toggle("selected", c === card);
        });
        updateTemplatesMainButton();
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

  function sendSelectedTemplate() {
    if (!tg || !selectedTemplateId) return;
    // tg.sendData доставляет выбор боту как обычное сообщение
    // (web_app_data) и закрывает Mini App — стандартный способ Telegram
    // вернуть результат выбора туда, откуда открыли.
    tg.sendData(JSON.stringify({ template_id: selectedTemplateId }));
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
      if (typeof tg.showAlert === "function") {
        tg.showAlert(
          "Отправьте боту команду /upload_template и приложите файл образца " +
          "(.doc или .docx) — он появится в этом списке.",
          function () { tg.close(); }
        );
      } else {
        tg.close();
      }
    });

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
        return "<td>" + escapeHtml(row[c.key]) + "</td>";
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
  // Точка входа: ?preview=<id> -> экран предпросмотра, иначе шаблоны
  // =====================================================================

  function main() {
    initTelegram();
    const params = new URLSearchParams(window.location.search);
    const previewId = params.get("preview");
    if (previewId) {
      initPreviewScreen(previewId);
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
