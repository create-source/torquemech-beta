(() => {
  const state = {
    language: document.documentElement.dataset.language || "en-US",
    translations: {},
    exactText: {},
  };

  const skipTags = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "CODE", "PRE"]);

  function loadPayload(payload) {
    const next = payload || window.TM_I18N || {};
    state.language = next.language || document.documentElement.dataset.language || "en-US";
    state.translations = next.translations || {};
    state.exactText = next.exactText || {};
  }

  function translate(key, fallback) {
    return state.translations[key] || state.exactText[fallback || key] || fallback || key;
  }

  function translateExactText(value) {
    const clean = String(value || "").replace(/\s+/g, " ").trim();
    if (!clean) return "";
    if (state.exactText[clean]) return state.exactText[clean];

    const countMatch = clean.match(/^(\d+)\s+(customer|customers|vehicle|vehicles|item|items)$/i);
    if (countMatch) {
      const count = countMatch[1];
      const noun = countMatch[2].toLowerCase();
      const translated = translate(`ui.${noun === "customers" ? "customers_lower" : noun === "vehicles" ? "vehicles_lower" : noun}`);
      return `${count} ${translated}`;
    }

    return "";
  }

  function applyNodeTranslation(root = document) {
    loadPayload();
    document.documentElement.lang = state.language === "es" ? "es" : "en-US";
    document.documentElement.dataset.language = state.language;

    root.querySelectorAll("[data-i18n]").forEach((node) => {
      const key = node.dataset.i18n;
      const value = translate(key, node.textContent.trim());
      if (value) node.textContent = value;
    });

    root.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
      const key = node.dataset.i18nPlaceholder;
      const value = translate(key, node.getAttribute("placeholder") || "");
      if (value) node.setAttribute("placeholder", value);
    });

    const walker = document.createTreeWalker(root.body || root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || skipTags.has(parent.tagName) || parent.closest("[data-no-i18n]")) {
          return NodeFilter.FILTER_REJECT;
        }
        if (!parent.closest(".tm-menu, .tm-notification, .tm-footer, .tm-pro-shell, .tm-account-page, .tm-modal, .tm-feedback-reminder")) {
          return NodeFilter.FILTER_REJECT;
        }
        return translateExactText(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    });

    const replacements = [];
    while (walker.nextNode()) {
      const translated = translateExactText(walker.currentNode.nodeValue);
      if (translated) replacements.push([walker.currentNode, translated]);
    }
    replacements.forEach(([node, translated]) => {
      const prefix = String(node.nodeValue || "").match(/^\s*/)?.[0] || "";
      const suffix = String(node.nodeValue || "").match(/\s*$/)?.[0] || "";
      node.nodeValue = `${prefix}${translated}${suffix}`;
    });
  }

  window.tmI18n = {
    apply: applyNodeTranslation,
    load(payload) {
      window.TM_I18N = payload || {};
      loadPayload(window.TM_I18N);
      applyNodeTranslation(document);
    },
    translate,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => applyNodeTranslation(document), { once: true });
  } else {
    applyNodeTranslation(document);
  }
})();
