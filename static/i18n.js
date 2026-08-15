(() => {
  const state = {
    language: document.documentElement.dataset.language || "en",
    locale: { code: "en", dir: "ltr" },
    translations: {},
    fallbackTranslations: {},
    exactText: {},
  };

  const skipTags = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "CODE", "PRE"]);

  function loadPayload(payload) {
    const next = payload || window.TM_I18N || {};
    state.language = next.language || document.documentElement.dataset.language || "en";
    state.locale = next.locale || { code: state.language, dir: "ltr" };
    state.translations = next.translations || {};
    state.fallbackTranslations = next.fallbackTranslations || {};
    state.exactText = next.exactText || {};
  }

  function interpolate(value, params) {
    let output = String(value == null ? "" : value);
    Object.entries(params || {}).forEach(([key, replacement]) => {
      output = output.replaceAll(`{${key}}`, String(replacement == null ? "" : replacement));
    });
    return output;
  }

  function translate(key, fallback, params) {
    return interpolate(state.translations[key] || state.fallbackTranslations[key] || state.exactText[fallback || key] || fallback || key, params);
  }

  function translateExactText(value) {
    const clean = String(value || "").replace(/\s+/g, " ").trim();
    if (!clean) return "";
    if (state.exactText[clean]) return state.exactText[clean];

    const countMatch = clean.match(/^(\d+)\s+(customer|customers|vehicle|vehicles|item|items)$/i);

    if (countMatch) {
      const count = countMatch[1];
      const noun = countMatch[2].toLowerCase();

      const key =
        noun === "customers"
          ? "ui.customers_lower"
          : noun === "vehicles"
            ? "ui.vehicles_lower"
            : `ui.${noun}`;

      const translated = translate(key, noun);

      return `${count} ${translated}`;
    }

    return "";
  }

  function translatePreservingWhitespace(value) {
    const translated = translateExactText(value);
    if (!translated) return "";
    const prefix = String(value || "").match(/^\s*/)?.[0] || "";
    const suffix = String(value || "").match(/\s*$/)?.[0] || "";
    return `${prefix}${translated}${suffix}`;
  }

  function translateAttribute(node, attributeName) {
    const current = node.getAttribute(attributeName);
    const translated = translateExactText(current);
    if (translated && translated !== current) node.setAttribute(attributeName, translated);
  }

  function applyAttributeTranslations(root) {
    const scope = root.querySelectorAll ? root : document;
    scope.querySelectorAll("[placeholder]").forEach((node) => translateAttribute(node, "placeholder"));
    scope.querySelectorAll("[aria-label]").forEach((node) => translateAttribute(node, "aria-label"));
    scope.querySelectorAll("[title]").forEach((node) => translateAttribute(node, "title"));
    scope.querySelectorAll("input[type='button'], input[type='submit'], input[type='reset']").forEach((node) => {
      const translated = translateExactText(node.value);
      if (translated) node.value = translated;
    });
    scope.querySelectorAll("option").forEach((node) => {
      const translated = translatePreservingWhitespace(node.textContent);
      if (translated) node.textContent = translated;
    });
  }

  function applyNodeTranslation(root = document) {
    loadPayload();
    document.documentElement.lang = state.locale.code || state.language || "en";
    document.documentElement.dir = state.locale.dir || "ltr";
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

    root.querySelectorAll("[data-i18n-aria-label]").forEach((node) => {
      const key = node.dataset.i18nAriaLabel;
      const value = translate(key, node.getAttribute("aria-label") || "");
      if (value) node.setAttribute("aria-label", value);
    });

    root.querySelectorAll("[data-i18n-title]").forEach((node) => {
      const key = node.dataset.i18nTitle;
      const value = translate(key, node.getAttribute("title") || "");
      if (value) node.setAttribute("title", value);
    });

    root.querySelectorAll("[data-i18n-data-ready-label]").forEach((node) => {
      const key = node.dataset.i18nDataReadyLabel;
      const value = translate(key, node.dataset.readyLabel || "");
      if (value) node.dataset.readyLabel = value;
    });

    applyAttributeTranslations(root);

    const walker = document.createTreeWalker(root.body || root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || skipTags.has(parent.tagName) || parent.closest("[data-no-i18n]")) {
          return NodeFilter.FILTER_REJECT;
        }
        if (!parent.closest(".tm-menu, .tm-notification, .tm-footer, .tm-pro-shell, .tm-account-page, .tm-estimator-page, .tm-modal, .tm-feedback-reminder, .tm-public-estimate-page, .tm-public-estimate-unavailable-page, .tm-book-page, .tm-auth-page, .tm-quick-find-page, .tm-parts-page")) {
          return NodeFilter.FILTER_REJECT;
        }
        return translateExactText(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    });

    const replacements = [];
    while (walker.nextNode()) {
      const translated = translatePreservingWhitespace(walker.currentNode.nodeValue);
      if (translated) replacements.push([walker.currentNode, translated]);
    }
    replacements.forEach(([node, translated]) => {
      node.nodeValue = translated;
    });
  }

  let observer = null;
  let applying = false;
  let dialogsPatched = false;

  function translateDialogText(value) {
    const exact = translateExactText(value);
    if (exact) return exact;
    const photoLimit = String(value || "").match(/^You can attach up to (\d+) photos\.$/);
    if (photoLimit) return `Puedes adjuntar hasta ${photoLimit[1]} fotos.`;
    return value;
  }

  function patchDialogs() {
    if (dialogsPatched || state.language === "en") return;
    dialogsPatched = true;
    const nativeAlert = window.alert;
    const nativeConfirm = window.confirm;
    window.alert = function tmLocalizedAlert(message) {
      return nativeAlert.call(window, translateDialogText(message));
    };
    window.confirm = function tmLocalizedConfirm(message) {
      return nativeConfirm.call(window, translateDialogText(message));
    };
  }

  function startObserver() {
    if (observer || !window.MutationObserver || state.language === "en" || !document.body) return;
    observer = new MutationObserver((mutations) => {
      if (applying) return;
      const roots = [];
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) roots.push(node);
        });
        if (mutation.type === "attributes" && mutation.target?.nodeType === Node.ELEMENT_NODE) {
          roots.push(mutation.target);
        }
      });
      if (!roots.length) return;
      applying = true;
      roots.forEach((node) => applyNodeTranslation(node));
      applying = false;
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["placeholder", "aria-label", "title", "value"],
    });
  }

  window.tmI18n = {
    apply: applyNodeTranslation,
    load(payload) {
      window.TM_I18N = payload || {};
      loadPayload(window.TM_I18N);
      applyNodeTranslation(document);
      patchDialogs();
      startObserver();
    },
    translate,
    translateText(value) {
      return translateExactText(value) || value;
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      applyNodeTranslation(document);
      patchDialogs();
      startObserver();
    }, { once: true });
  } else {
    applyNodeTranslation(document);
    patchDialogs();
    startObserver();
  }
})();
