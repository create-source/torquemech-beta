document.addEventListener("DOMContentLoaded", () => {
  const ACTIVE_MS_KEY = "tm_feedback_active_ms";
  const SESSION_SHOWN_KEY = "tm_feedback_reminder_shown";
  const DISMISS_UNTIL_KEY = "tm_feedback_reminder_dismissed_until";
  const SHOW_AFTER_MS = 60000;
  const DISMISS_FOR_MS = 7 * 24 * 60 * 60 * 1000;
  const footerBtn = document.getElementById("feedbackBtn");
  const modal = document.getElementById("feedbackModal");
  const closeBtn = document.getElementById("feedbackCloseBtn");
  const reminder = document.getElementById("feedbackReminder");
  const reminderSendBtn = document.getElementById("feedbackReminderSend");
  const reminderLaterBtn = document.getElementById("feedbackReminderLater");
  const reminderCloseBtn = document.getElementById("feedbackReminderClose");

  const form = document.getElementById("feedbackForm");
  const nameEl = document.getElementById("fbName");
  const emailEl = document.getElementById("fbEmail");
  const msgEl = document.getElementById("fbMessage");
  const statusEl = document.getElementById("fbStatus");
  const submitBtn = document.getElementById("fbSubmitBtn");

  if (!footerBtn || !modal) return;

  let activeTimer = null;

  function getDismissedUntil() {
    return Number(localStorage.getItem(DISMISS_UNTIL_KEY) || "0");
  }

  function isDismissed() {
    return getDismissedUntil() > Date.now();
  }

  function isReminderShownThisSession() {
    return sessionStorage.getItem(SESSION_SHOWN_KEY) === "1";
  }

  function getActiveMs() {
    return Number(sessionStorage.getItem(ACTIVE_MS_KEY) || "0");
  }

  function setActiveMs(value) {
    sessionStorage.setItem(ACTIVE_MS_KEY, String(Math.max(0, Math.floor(value))));
  }

  function isModalOpen() {
    return !modal.hidden;
  }

  function hideReminder() {
    if (reminder) {
      reminder.hidden = true;
    }
  }

  function markReminderShown() {
    sessionStorage.setItem(SESSION_SHOWN_KEY, "1");
  }

  function dismissReminderForSevenDays() {
    localStorage.setItem(DISMISS_UNTIL_KEY, String(Date.now() + DISMISS_FOR_MS));
    markReminderShown();
    hideReminder();
  }

  function maybeShowReminder() {
    if (!reminder || reminder.hidden === false) return;
    if (document.hidden) return;
    if (isModalOpen()) return;
    if (isReminderShownThisSession()) return;
    if (isDismissed()) return;
    if (getActiveMs() < SHOW_AFTER_MS) return;

    reminder.hidden = false;
    markReminderShown();
  }

  function startActiveTimer() {
    if (activeTimer || document.hidden) return;

    activeTimer = window.setInterval(() => {
      setActiveMs(getActiveMs() + 1000);
      maybeShowReminder();
    }, 1000);
  }

  function stopActiveTimer() {
    if (!activeTimer) return;
    window.clearInterval(activeTimer);
    activeTimer = null;
  }

  function openModal() {
    hideReminder();
    markReminderShown();
    modal.hidden = false;
    document.body.classList.add("modal-open");
    statusEl.textContent = "";
    setTimeout(() => msgEl?.focus(), 0);
  }

  function closeModal() {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  footerBtn?.addEventListener("click", openModal);
  closeBtn?.addEventListener("click", closeModal);
  reminderSendBtn?.addEventListener("click", openModal);
  reminderLaterBtn?.addEventListener("click", dismissReminderForSevenDays);
  reminderCloseBtn?.addEventListener("click", dismissReminderForSevenDays);

  modal.addEventListener("click", (e) => {
    const t = e.target;
    if (t && t.getAttribute && t.getAttribute("data-close") === "1") closeModal();
  });

  document.addEventListener("keydown", (e) => {
    if (!modal.hidden && e.key === "Escape") closeModal();
  });

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();

    const message = (msgEl?.value || "").trim();
    if (!message) {
      statusEl.textContent = "Please enter a message.";
      return;
    }

    submitBtn.disabled = true;
    statusEl.textContent = "Sending…";

    try {
      const payload = {
        name: (nameEl?.value || "").trim() || null,
        email: (emailEl?.value || "").trim() || null,
        message
      };

      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!res.ok) throw new Error("Request failed");

      statusEl.textContent = "Thanks — repair intelligence feedback received.";
      form.reset();

      setTimeout(() => closeModal(), 700);
    } catch (err) {
      statusEl.textContent = "Could not send. Please try again.";
    } finally {
      submitBtn.disabled = false;
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopActiveTimer();
      return;
    }

    startActiveTimer();
    maybeShowReminder();
  });

  window.addEventListener("pagehide", () => {
    stopActiveTimer();
  });

  startActiveTimer();
  maybeShowReminder();

});
