document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("menuBtn");
  const menu = document.getElementById("navMenu");
  const backdrop = document.getElementById("menuBackdrop");
  const resourcesBtn = document.getElementById("resourcesMenuBtn");
  const resourcesPanel = document.getElementById("resourcesMenuPanel");

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

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeMenu();
    }
  });
});