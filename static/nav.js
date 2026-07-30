document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("menuBtn");
  const menu = document.getElementById("navMenu");
  const backdrop = document.getElementById("menuBackdrop");
  const resourcesBtn = document.getElementById("resourcesMenuBtn");
  const resourcesPanel = document.getElementById("resourcesMenuPanel");
  const notificationBtn = document.getElementById("notificationBtn");
  const notificationPanel = document.getElementById("notificationPanel");

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

  function openMenu() {
    closeNotifications();
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
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
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
    }
  });

  document.addEventListener("click", (e) => {
    if (!notificationBtn || !notificationPanel || notificationPanel.hidden) return;
    if (notificationPanel.contains(e.target) || notificationBtn.contains(e.target)) return;
    closeNotifications();
  });
});
