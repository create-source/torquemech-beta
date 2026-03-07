async function mountNav() {
  const mount = document.getElementById("navMount");
  if (!mount) return;

  try {
    const res = await fetch("/static/partials/nav.html?v=2", { cache: "no-store" });
    if (!res.ok) throw new Error("nav fetch failed");
    mount.innerHTML = await res.text();
  } catch (e) {
    mount.innerHTML = "";
  }
}

function initFeedback() {
  const fab = document.getElementById("feedbackFab");
  const backdrop = document.getElementById("feedbackBackdrop");
  const closeBtn = document.getElementById("feedbackClose");
  const cancelBtn = document.getElementById("feedbackCancel");
  const form = document.getElementById("feedbackForm");
  const text = document.getElementById("feedbackText");

  const open = () => { backdrop.hidden = false; setTimeout(() => text?.focus(), 0); };
  const close = () => { backdrop.hidden = true; };

  fab?.addEventListener("click", open);
  closeBtn?.addEventListener("click", close);
  cancelBtn?.addEventListener("click", close);

  backdrop?.addEventListener("click", (e) => {
    if (e.target === backdrop) close();
  });

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = (text?.value || "").trim();
    if (!message) return;

    const subject = encodeURIComponent("TorqueMech Beta Feedback");
    const body = encodeURIComponent(message);
    window.location.href = `mailto:?subject=${subject}&body=${body}`;

    text.value = "";
    close();
  });
}

function initYear() {
  const el = document.getElementById("year");
  if (el) el.textContent = new Date().getFullYear();
}

(async function boot() {
  await mountNav();
  window.initTorqueMechNav?.();
  initYear();
})();