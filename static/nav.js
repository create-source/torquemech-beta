document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("menuBtn");
  const menu = document.getElementById("navMenu");
  const backdrop = document.getElementById("menuBackdrop");
  const resourcesBtn = document.getElementById("resourcesMenuBtn");
  const resourcesPanel = document.getElementById("resourcesMenuPanel");
  const notificationBtn = document.getElementById("notificationBtn");
  const notificationPanel = document.getElementById("notificationPanel");
  const globalSearchBtn = document.getElementById("globalSearchBtn");
  const globalSearchPanel = document.getElementById("globalSearchPanel");
  const globalSearchInput = document.getElementById("globalSearchInput");
  const globalSearchClose = document.getElementById("globalSearchClose");

  const globalSearchResults = document.getElementById("globalSearchResults");

  const mobileSearchBtn = document.getElementById("mobileSearchBtn");
  const mobileMoreBtn = document.getElementById("mobileMoreBtn");
  const mobileNotificationsBtn = document.getElementById("mobileNotificationsBtn");
  const mobileNavItems = document.querySelectorAll("[data-mobile-nav]");

  let globalSearchTimer = null;
  let globalSearchController = null;

  if (!btn || !menu || !backdrop) {
    console.warn("Nav elements not found");
    return;
  }

  function collapseResources() {
    if (!resourcesBtn || !resourcesPanel) return;
    resourcesBtn.setAttribute("aria-expanded", "false");
    resourcesPanel.hidden = true;
  }

  function toggleResources() {
    if (!resourcesBtn || !resourcesPanel) return;
    const expanded = resourcesBtn.getAttribute("aria-expanded") === "true";
    resourcesBtn.setAttribute("aria-expanded", String(!expanded));
    resourcesPanel.hidden = expanded;
  }

  function escapeSearchHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function globalSearchHint() {
    if (!globalSearchResults) return;

    globalSearchResults.innerHTML = `
      <div class="tm-globalSearchPanel__hint">
        Search customers, vehicles, VINs, plates, appointments, estimates, invoices, OBD codes, symptoms, or repair terms.
      </div>
    `;
  }

  function globalSearchLoading() {
    if (!globalSearchResults) return;

    globalSearchResults.innerHTML = `
      <div class="tm-globalSearchPanel__hint">
        Searching...
      </div>
    `;
  }

  function globalSearchEmpty(query) {
    if (!globalSearchResults) return;

    globalSearchResults.innerHTML = `
      <div class="tm-globalSearchPanel__hint">
        No results found for <strong>${escapeSearchHtml(query)}</strong>.
      </div>
    `;
  }

  function renderGlobalSearchGroup(label, items) {
    if (!Array.isArray(items) || items.length === 0) return "";

    const rows = items.map((item) => {
      const status = item.status
        ? `<span class="tm-globalSearchResult__status">${escapeSearchHtml(item.status)}</span>`
        : "";

      const subtitle = item.subtitle
        ? `<div class="tm-globalSearchResult__subtitle">${escapeSearchHtml(item.subtitle)}</div>`
        : "";

      return `
        <a
          class="tm-globalSearchResult"
          href="${escapeSearchHtml(item.url || "#")}"
        >
          <div class="tm-globalSearchResult__main">
            <div class="tm-globalSearchResult__title">
              ${escapeSearchHtml(item.title || "Result")}
            </div>
            ${subtitle}
          </div>
          ${status}
        </a>
      `;
    }).join("");

    return `
      <section class="tm-globalSearchGroup">
        <div class="tm-globalSearchGroup__title">${escapeSearchHtml(label)}</div>
        <div class="tm-globalSearchGroup__items">
          ${rows}
        </div>
      </section>
    `;
  }

  function renderGlobalSearchResults(payload) {
    if (!globalSearchResults) return;

    const groups = payload?.groups || {};
    const diagnostics = groups.diagnostics || [];
    const customers = groups.customers || [];
    const vehicles = groups.vehicles || [];
    const appointments = groups.appointments || [];
    const estimates = groups.estimates || [];
    const repairs = groups.repairs || [];
    const invoices = groups.invoices || [];

    const total =
      diagnostics.length +
      customers.length +
      vehicles.length +
      appointments.length +
      estimates.length +
      repairs.length +
      invoices.length;

    if (total === 0) {
      globalSearchEmpty(payload?.query || globalSearchInput?.value || "");
      return;
    }

    globalSearchResults.innerHTML = [
      renderGlobalSearchGroup("Diagnostics", diagnostics),
      renderGlobalSearchGroup("Customers", customers),
      renderGlobalSearchGroup("Vehicles", vehicles),
      renderGlobalSearchGroup("Appointments", appointments),
      renderGlobalSearchGroup("Estimates", estimates),
      renderGlobalSearchGroup("Repairs", repairs),
      renderGlobalSearchGroup("Invoices", invoices),
    ].join("");
  }

  async function runGlobalSearch(query) {
    const cleanQuery = String(query || "").trim();

    if (cleanQuery.length < 2) {
      if (globalSearchController) {
        globalSearchController.abort();
        globalSearchController = null;
      }

      globalSearchHint();
      return;
    }

    if (globalSearchController) {
      globalSearchController.abort();
    }

    globalSearchController = new AbortController();

    globalSearchLoading();

    try {
      const response = await fetch(
        `/pro/global-search?q=${encodeURIComponent(cleanQuery)}`,
        {
          method: "GET",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          signal: globalSearchController.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`Search failed with ${response.status}`);
      }

      const payload = await response.json();

      if (
        String(globalSearchInput?.value || "").trim() !== cleanQuery
      ) {
        return;
      }

      renderGlobalSearchResults(payload);
    } catch (error) {
      if (error?.name === "AbortError") return;

      if (globalSearchResults) {
        globalSearchResults.innerHTML = `
          <div class="tm-globalSearchPanel__hint">
            Search could not be loaded. Please try again.
          </div>
        `;
      }
    }
  }

  function openGlobalSearch() {
    if (!globalSearchBtn || !globalSearchPanel) return;

    closeMenu();
    closeNotifications();

    globalSearchPanel.hidden = false;
    globalSearchBtn.setAttribute("aria-expanded", "true");

    window.setTimeout(() => {
      globalSearchInput?.focus();
    }, 0);
  }

  function closeGlobalSearch(options = {}) {
    if (!globalSearchBtn || !globalSearchPanel) return;

    globalSearchPanel.hidden = true;
    globalSearchBtn.setAttribute("aria-expanded", "false");

    if (globalSearchInput) {
      globalSearchInput.value = "";
    }

    window.clearTimeout(globalSearchTimer);

    if (globalSearchController) {
      globalSearchController.abort();
      globalSearchController = null;
    }

    globalSearchHint();

    if (options.returnFocus) {
      globalSearchBtn.focus();
    }
  }

  function openMenu() {
    closeNotifications();
    closeGlobalSearch();
    collapseResources();
    menu.hidden = false;
    backdrop.hidden = false;
    btn.setAttribute("aria-expanded", "true");
  }

  function closeMenu() {
    menu.hidden = true;
    backdrop.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    collapseResources();
  }

  function firstNotificationFocusTarget() {
    if (!notificationPanel) return null;
    return notificationPanel.querySelector("button, a, input, [tabindex]:not([tabindex='-1'])");
  }

  function openNotifications() {
    if (!notificationBtn || !notificationPanel) return;
    closeMenu();
    closeGlobalSearch();
    notificationPanel.hidden = false;
    notificationBtn.setAttribute("aria-expanded", "true");
    const focusTarget = firstNotificationFocusTarget();
    if (focusTarget) focusTarget.focus();
  }

  function closeNotifications(options = {}) {
    if (!notificationBtn || !notificationPanel) return;
    if (notificationPanel.hidden) return;
    notificationPanel.hidden = true;
    notificationBtn.setAttribute("aria-expanded", "false");
    if (options.returnFocus) notificationBtn.focus();
  }

  function notificationUnreadCount() {
    if (!notificationBtn) return 0;
    const match = (notificationBtn.getAttribute("aria-label") || "").match(/(\d+) unread/);
    return match ? Number.parseInt(match[1], 10) || 0 : 0;
  }

  function setNotificationUnreadCount(nextCount) {
    if (!notificationBtn || !notificationPanel) return;
    const safeCount = Math.max(0, nextCount);
    notificationBtn.setAttribute("aria-label", `Notifications, ${safeCount} unread`);
    const panelCount = notificationPanel.querySelector(".tm-notificationPanel__head span");
    if (panelCount) panelCount.textContent = `${safeCount} unread`;
    const badge = notificationBtn.querySelector(".tm-notificationBtn__badge");
    if (badge) {
      if (safeCount === 0) {
        badge.remove();
      } else {
        badge.textContent = safeCount > 99 ? "99+" : String(safeCount);
        badge.setAttribute("aria-label", `${safeCount} unread notifications`);
      }
    }
    if (mobileMoreBtn) {
      mobileMoreBtn.setAttribute(
        "aria-label",
        safeCount > 0
          ? `Open more navigation, ${safeCount} unread notifications`
          : "Open more navigation"
      );

      let mobileBadge = document.getElementById("mobileNotificationBadge");

      if (safeCount === 0) {
        mobileBadge?.remove();
      } else {
        if (!mobileBadge) {
          const moreIcon = mobileMoreBtn.querySelector(
            ".tm-mobileDock__icon--more"
          );

          if (moreIcon) {
            mobileBadge = document.createElement("span");
            mobileBadge.className = "tm-mobileDock__badge";
            mobileBadge.id = "mobileNotificationBadge";
            moreIcon.appendChild(mobileBadge);
          }
        }

        if (mobileBadge) {
          mobileBadge.textContent =
            safeCount > 99 ? "99+" : String(safeCount);

          mobileBadge.setAttribute(
            "aria-label",
            `${safeCount} unread notifications`
          );
        }
      }
    }

    if (mobileNotificationsBtn) {
      mobileNotificationsBtn.setAttribute(
        "aria-label",
        `Notifications, ${safeCount} unread`
      );

      const menuCount = document.getElementById(
        "mobileNotificationMenuCount"
      );

      if (menuCount) {
        menuCount.textContent = `${safeCount} unread`;
      }
    }
  }

  function showNotificationEmptyState() {
    if (!notificationPanel) return;
    const list = notificationPanel.querySelector(".tm-notificationList");
    if (!list || list.querySelector("[data-notification-item]")) return;
    list.remove();
    const empty = document.createElement("p");
    empty.className = "tm-notificationEmpty";
    empty.textContent = "No new notifications.";
    notificationPanel.appendChild(empty);
  }

  function removeNotificationItem(item) {
    if (!item) return;
    const wasUnread = item.dataset.notificationUnread === "true";
    item.remove();
    if (wasUnread) setNotificationUnreadCount(notificationUnreadCount() - 1);
    showNotificationEmptyState();
  }

  async function dismissNotification(form, submitter) {
    if (!window.fetch) return false;
    if (form.dataset.notificationDismissPending === "true") return true;
    const item = form.closest("[data-notification-card], [data-notification-item]");
    form.dataset.notificationDismissPending = "true";
    form.classList.remove("is-error");
    if (submitter) submitter.disabled = true;
    try {
      const formData = new FormData(form);
      const body = new URLSearchParams();
      for (const [name, value] of formData.entries()) {
        if (typeof value === "string") {
          body.append(name, value);
        }
      }
      const response = await fetch(form.action, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });

      
      let payload = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      if (!response.ok || !payload || payload.ok !== true) throw new Error("Dismiss failed");
      removeNotificationItem(item);
      if (Number.isFinite(Number(payload.unread_count))) {
        setNotificationUnreadCount(Number(payload.unread_count));
      }
      return true;
    } catch (error) {
      form.classList.add("is-error");
      if (submitter) submitter.disabled = false;
      form.dataset.notificationDismissPending = "false";
      return true;
    }
  }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();

    if (menu.hidden) {
      openMenu();
    } else {
      closeMenu();
    }
  });

  backdrop.addEventListener("click", closeMenu);

  menu.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", closeMenu);
  });

  document.addEventListener("click", (e) => {
    if (menu.hidden) return;
    if (menu.contains(e.target) || btn.contains(e.target)) return;

    closeMenu();
  });

  if (resourcesBtn && resourcesPanel) {
    collapseResources();

    resourcesBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleResources();
    });
  }

  if (globalSearchBtn && globalSearchPanel) {
    globalSearchBtn.addEventListener("click", (e) => {
      e.stopPropagation();

      if (globalSearchPanel.hidden) {
        openGlobalSearch();
      } else {
        closeGlobalSearch({ returnFocus: true });
      }
    });

    globalSearchPanel.addEventListener("click", (e) => {
      e.stopPropagation();
    });

    globalSearchClose?.addEventListener("click", () => {
      closeGlobalSearch({ returnFocus: true });
    });

    globalSearchInput?.addEventListener("input", () => {
      window.clearTimeout(globalSearchTimer);

      const query = globalSearchInput.value.trim();

      if (query.length < 2) {
        runGlobalSearch(query);
        return;
      }

      globalSearchTimer = window.setTimeout(() => {
        runGlobalSearch(query);
      }, 250);
    });
  }

  if (notificationBtn && notificationPanel) {
    notificationBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (notificationPanel.hidden) {
        openNotifications();
      } else {
        closeNotifications({ returnFocus: true });
      }
    });

    notificationPanel.addEventListener("click", (e) => {
      e.stopPropagation();
    });

    notificationPanel.addEventListener("submit", (e) => {
      const form = e.target.closest("[data-notification-dismiss-form]");
      if (!form || !notificationPanel.contains(form)) return;
      const submitter = e.submitter || form.querySelector("[type='submit']");
      if (!window.fetch) return;
      e.preventDefault();
      dismissNotification(form, submitter);
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeMenu();
      closeNotifications({ returnFocus: true });
      closeGlobalSearch({ returnFocus: true });
    }
  });

  document.addEventListener("click", (e) => {
    if (!notificationBtn || !notificationPanel || notificationPanel.hidden) return;
    if (notificationPanel.contains(e.target) || notificationBtn.contains(e.target)) return;
    closeNotifications();
  });

  document.addEventListener("click", (e) => {
    if (!globalSearchBtn || !globalSearchPanel || globalSearchPanel.hidden) return;
    if (globalSearchPanel.contains(e.target) || globalSearchBtn.contains(e.target)) return;

    closeGlobalSearch();
  });

  if (mobileSearchBtn) {
    mobileSearchBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openGlobalSearch();
    });
  }

  if (mobileMoreBtn) {
    mobileMoreBtn.addEventListener("click", (e) => {
      e.stopPropagation();

      if (menu.hidden) {
        openMenu();
      } else {
        closeMenu();
      }
    });
  }

  if (mobileNotificationsBtn) {
    mobileNotificationsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openNotifications();
    });
  }

      function setMobileNavActiveState() {
        const path = window.location.pathname;

        mobileNavItems.forEach((item) => {
          item.classList.remove("is-active");
          item.removeAttribute("aria-current");
        });

        let key = "";

        if (path === "/pro/dashboard" || path === "/pro") {
          key = "hub";
        } else if (path.startsWith("/pro/calendar")) {
          key = "schedule";
        } else if (path.startsWith("/pro/customers")) {
          key = "customers";
        }

        if (!key) return;

        const activeItem = document.querySelector(
          `[data-mobile-nav="${key}"]`
        );

        if (activeItem) {
          activeItem.classList.add("is-active");
          activeItem.setAttribute("aria-current", "page");
        }
      }

      setMobileNavActiveState();
});
