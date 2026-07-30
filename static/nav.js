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
    closeOpenNotificationSwipe();
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
    if (openNotificationSwipe === item) openNotificationSwipe = null;
    const wasUnread = item.dataset.notificationUnread === "true";
    item.remove();
    if (wasUnread) setNotificationUnreadCount(notificationUnreadCount() - 1);
    showNotificationEmptyState();
  }

  function wireNotificationDismissal() {
    if (!notificationPanel) return;
    notificationPanel.querySelectorAll("[data-notification-dismiss-form]").forEach((form) => {
      form.addEventListener("click", (e) => {
        if (form.classList.contains("tm-notificationItem__swipeDismiss")) e.stopPropagation();
      });

      form.addEventListener("submit", async (e) => {
        if (!window.fetch) return;
        e.preventDefault();
        const item = form.closest("[data-notification-item]");
        const submitter = e.submitter;
        if (submitter) submitter.disabled = true;
        try {
          const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            credentials: "same-origin",
            headers: { "X-Requested-With": "fetch" },
          });
          if (!response.ok) throw new Error("Dismiss failed");
          removeNotificationItem(item);
        } catch (error) {
          form.submit();
        }
      });
    });
  }

  const notificationSwipeReveal = 76;
  const notificationSwipeThreshold = 46;
  const notificationSwipeDirectionThreshold = 8;
  const notificationSwipeHorizontalRatio = 1.35;
  let openNotificationSwipe = null;

  function canUseNotificationSwipe() {
    const touchCapable = window.matchMedia("(hover: none) and (pointer: coarse)").matches || navigator.maxTouchPoints > 0;
    const mobileLayout = window.matchMedia("(max-width: 640px)").matches;
    return touchCapable && mobileLayout;
  }

  function setNotificationSwipeOffset(item, offset) {
    const card = item ? item.querySelector(".tm-notificationItem") : null;
    if (!card) return;
    const clamped = Math.min(0, Math.max(-notificationSwipeReveal, offset));
    card.style.transform = clamped === 0 ? "" : `translateX(${clamped}px)`;
  }

  function closeNotificationSwipe(item) {
    if (!item) return;
    item.classList.remove("is-dragging");
    item.classList.remove("is-swiped");
    setNotificationSwipeOffset(item, 0);
    if (openNotificationSwipe === item) openNotificationSwipe = null;
  }

  function openNotificationSwipeItem(item) {
    if (!item) return;
    if (openNotificationSwipe && openNotificationSwipe !== item) closeNotificationSwipe(openNotificationSwipe);
    item.classList.remove("is-dragging");
    item.classList.add("is-swiped");
    setNotificationSwipeOffset(item, -notificationSwipeReveal);
    openNotificationSwipe = item;
  }

  function closeOpenNotificationSwipe() {
    closeNotificationSwipe(openNotificationSwipe);
  }

  function notificationSwipeOffset(startOffset, deltaX) {
    return Math.min(0, Math.max(-notificationSwipeReveal, startOffset + deltaX));
  }

  function wireNotificationSwipeActions() {
    if (!notificationPanel) return;
    notificationPanel.querySelectorAll("[data-notification-item]").forEach((item) => {
      let startX = 0;
      let startY = 0;
      let startOffset = 0;
      let active = false;
      let gestureDirection = null;

      item.addEventListener("touchstart", (e) => {
        if (!canUseNotificationSwipe()) return;
        const touch = e.touches[0];
        startX = touch.clientX;
        startY = touch.clientY;
        startOffset = openNotificationSwipe === item ? -notificationSwipeReveal : 0;
        active = true;
        gestureDirection = null;
        if (openNotificationSwipe && openNotificationSwipe !== item) closeNotificationSwipe(openNotificationSwipe);
      }, { passive: true });

      item.addEventListener("touchmove", (e) => {
        if (!active) return;
        const touch = e.touches[0];
        const deltaX = touch.clientX - startX;
        const deltaY = touch.clientY - startY;
        const absX = Math.abs(deltaX);
        const absY = Math.abs(deltaY);
        if (!gestureDirection) {
          if (Math.max(absX, absY) < notificationSwipeDirectionThreshold) return;
          if (absX > absY * notificationSwipeHorizontalRatio) {
            gestureDirection = "horizontal";
            item.classList.add("is-dragging");
          } else if (absY > absX) {
            gestureDirection = "vertical";
            active = false;
            closeNotificationSwipe(item);
            return;
          } else {
            return;
          }
        }

        if (gestureDirection !== "horizontal") return;
        e.preventDefault();
        setNotificationSwipeOffset(item, notificationSwipeOffset(startOffset, deltaX));
      }, { passive: false });

      item.addEventListener("touchend", (e) => {
        if (!active) return;
        active = false;
        item.classList.remove("is-dragging");
        const touch = e.changedTouches[0];
        const deltaX = touch.clientX - startX;
        const deltaY = touch.clientY - startY;
        if (gestureDirection === "horizontal" && Math.abs(deltaX) > Math.abs(deltaY) * notificationSwipeHorizontalRatio) {
          if (deltaX <= -notificationSwipeThreshold) {
            openNotificationSwipeItem(item);
          } else if (deltaX >= notificationSwipeThreshold) {
            closeNotificationSwipe(item);
          } else {
            closeNotificationSwipe(item);
          }
        } else {
          closeNotificationSwipe(item);
        }
      }, { passive: true });

      item.addEventListener("touchcancel", () => {
        active = false;
        item.classList.remove("is-dragging");
        closeNotificationSwipe(item);
      }, { passive: true });
    });
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
    wireNotificationDismissal();
    wireNotificationSwipeActions();

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
      if (openNotificationSwipe && !openNotificationSwipe.contains(e.target)) closeOpenNotificationSwipe();
    });

    notificationPanel.addEventListener("touchmove", (e) => {
      if (e.target.closest("[data-notification-item]")) return;
      closeOpenNotificationSwipe();
    }, { passive: true });

    notificationPanel.addEventListener("scroll", closeOpenNotificationSwipe, { passive: true });
  }

  window.addEventListener("scroll", closeOpenNotificationSwipe, { passive: true });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeMenu();
      closeNotifications({ returnFocus: true });
    }
  });

  document.addEventListener("click", (e) => {
    if (!notificationBtn || !notificationPanel || notificationPanel.hidden) return;
    if (notificationPanel.contains(e.target) || notificationBtn.contains(e.target)) return;
    closeOpenNotificationSwipe();
    closeNotifications();
  });
});
