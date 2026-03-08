document.addEventListener("DOMContentLoaded", () => {

  const fab = document.getElementById("feedbackFab");
  const modal = document.getElementById("feedbackModal");
  const closeBtn = document.getElementById("feedbackCloseBtn");

  const form = document.getElementById("feedbackForm");
  const nameEl = document.getElementById("fbName");
  const emailEl = document.getElementById("fbEmail");
  const msgEl = document.getElementById("fbMessage");
  const statusEl = document.getElementById("fbStatus");
  const submitBtn = document.getElementById("fbSubmitBtn");

  if (!fab || !modal) return;

  function openModal() {
    modal.hidden = false;
    statusEl.textContent = "";
    setTimeout(() => msgEl?.focus(), 0);
  }

  function closeModal() {
    modal.hidden = true;
  }

  fab.addEventListener("click", openModal);
  closeBtn?.addEventListener("click", closeModal);

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

      statusEl.textContent = "Thanks — feedback received.";
      form.reset();

      setTimeout(() => closeModal(), 700);
    } catch (err) {
      statusEl.textContent = "Could not send. Please try again.";
    } finally {
      submitBtn.disabled = false;
    }
    });

    // Show feedback hint on first visit
    const hint = document.getElementById("feedbackHint");

    if (hint && !localStorage.getItem("tm_feedback_hint_seen")) {

      setTimeout(() => {

        hint.hidden = false;

        requestAnimationFrame(() => {
          hint.classList.add("show");
        });

        setTimeout(() => {

          hint.classList.remove("show");

          setTimeout(() => {
            hint.hidden = true;
            localStorage.setItem("tm_feedback_hint_seen", "1");
          }, 250);

        }, 5000);

      }, 1800);

    }

});