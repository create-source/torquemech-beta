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
