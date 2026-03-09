/* 🔒 LOCKED (Beta Stabilization)
   Draft load / Generate All first-click fix is working.
   Do NOT edit draft functions unless absolutely necessary.
   If changes are needed, edit app.locked.js first and diff carefully.
*/
// static/app.js — CLEAN (Beta-stable)
(() => {
  // Only run on Estimator page
  const estimateBtn = document.getElementById("quickEstimateBtn");
  if (!estimateBtn) return;

  let SERVICES = {};         // full tree
  let SERVICE_INDEX = {};    // id -> service object for quick lookup

  async function loadServicesCatalog() {
    const res = await fetch("/api/services");
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    SERVICES = data;
    SERVICE_INDEX = {};

    // Build index
    Object.entries(SERVICES).forEach(([cat, subs]) => {
      Object.entries(subs).forEach(([sub, services]) => {
        services.forEach(svc => {
          SERVICE_INDEX[svc.id] = { ...svc, category: cat, subcategory: sub };
        });
      });
    });
  }

  function populateCategories(categoryEl) {
    categoryEl.innerHTML = `<option value="">Select category...</option>`;
    Object.keys(SERVICES).forEach(cat => {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      categoryEl.appendChild(opt);
    });
  }

  function populateSubcategories(categoryEl, subcategoryEl) {
    const cat = categoryEl.value;
    subcategoryEl.innerHTML = `<option value="">Select subcategory...</option>`;
    if (!cat || !SERVICES[cat]) return;

    Object.keys(SERVICES[cat]).forEach(sub => {
      const opt = document.createElement("option");
      opt.value = sub;
      opt.textContent = sub;
      subcategoryEl.appendChild(opt);
    });
  }

  function populateServices(categoryEl, subcategoryEl, serviceEl) {
    const cat = categoryEl.value;
    const sub = subcategoryEl.value;
    serviceEl.innerHTML = `<option value="">Select service...</option>`;
    if (!cat || !sub || !SERVICES[cat]?.[sub]) return;

    SERVICES[cat][sub].forEach(svc => {
      const opt = document.createElement("option");
      opt.value = svc.id;        // IMPORTANT: use id
      opt.textContent = svc.name;
      serviceEl.appendChild(opt);
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const categoryEl = document.querySelector("#svcCategory");
    const subcategoryEl = document.querySelector("#svcSubcategory");
    const serviceEl = document.querySelector("#svcService");

    if (!categoryEl || !subcategoryEl || !serviceEl) return;

    await loadServicesCatalog();
    populateCategories(categoryEl);

    categoryEl.addEventListener("change", () => {
      populateSubcategories(categoryEl, subcategoryEl);
      populateServices(categoryEl, subcategoryEl, serviceEl);
    });

    subcategoryEl.addEventListener("change", () => {
      populateServices(categoryEl, subcategoryEl, serviceEl);
    });
  });

  function addSelectedServiceToEstimate(serviceId) {
    const svc = SERVICE_INDEX[serviceId];
    if (!svc) return;

    const line = {
      type: "labor",
      service_id: svc.id,
      name: svc.name,
      category: svc.category,
      subcategory: svc.subcategory,
      hours: (svc.default_hours ?? 0),
      rate: getLaborRate(),          // from your UI
      notes: svc.notes || ""
    };

    // optional: auto-add part templates as suggestion lines (not priced)
    const parts = (svc.parts_templates || []).map(p => ({
      type: "part",
      name: p,
      qty: 1,
      unit_price: 0
    }));

    estimate.lines.push(line, ...parts);
    renderEstimate();
    persistDraft();
  }

  function calculateTotals(estimate) {
    const labor = estimate.lines
      .filter(l => l.type === "labor")
      .reduce((sum, l) => sum + (Number(l.hours) || 0) * (Number(l.rate) || 0), 0);

    const parts = estimate.lines
      .filter(l => l.type === "part")
      .reduce((sum, p) => sum + (Number(p.qty) || 0) * (Number(p.unit_price) || 0), 0);

    const taxRate = Number(estimate.tax_rate || 0); // e.g. 0.0825
    const suppliesRate = Number(estimate.supplies_rate || 0); // e.g. 0.05

    const supplies = parts * suppliesRate;
    const taxableParts = parts + supplies;  // common approach; can change later
    const tax = taxableParts * taxRate;

    const total = labor + parts + supplies + tax;

    return { labor, parts, supplies, tax, total };
  }

  // ---- DOM helpers ----
  const $ = (id) => document.getElementById(id);

  // Vehicle
  const yearEl = $("year");
  const makeEl = $("make");
  const modelEl = $("model");

  // Service selection
  const categoryEl = $("category");
  const serviceEl = $("service");

  // Inputs
  const laborHoursEl = $("laborHours");
  const laborHoursRangeEl = $("laborHoursRange");
  const partsPriceEl = $("partsPrice");
  const laborRateEl = $("laborRate");
  const notesEl = $("notes");

  // Buttons / UI
  const statusBox = $("statusBox");
  const clearBtn = $("clearBtn");
  const generateAllBtn = $("generateAllBtn");
  const addLineBtn = $("addLineBtn");
  const addServiceHint = $("addServiceHint");
  const getEstimateHint = $("getEstimateHint");
  const quickEstimateBtn = document.getElementById("quickEstimateBtn");

  // Line items
  const lineItemsWrap = $("lineItemsWrap");
  const lineItemsList = $("lineItemsList");

  // Preview (optional)
  const estimatePreview = $("estimatePreview");
  const previewTotalText = $("previewTotalText");
  const previewSubText = $("previewSubText");

  // Confirm modal
  const confirmModal = $("confirmModal");
  const confirmBackdrop = $("confirmBackdrop");
  const confirmCloseBtn = $("confirmCloseBtn");
  const confirmAddBtn = $("confirmAddBtn");
  const confirmMsg = $("confirmMsg");
  const confirmServiceText = $("confirmServiceText");
  const confirmTotalText = $("confirmTotalText");

  // Customer
  const customerNameEl = $("customerName");
  const customerPhoneEl = $("customerPhone");

  // VIN
  const vinEl = $("vin");
  const vinLookupBtn = $("vinLookupBtn");
  const vinToggle = $("vinToggle");
  const vinPanel = $("vinPanel");

  // PWA install (optional)
  const installBtn = $("installBtn");

  // Signature
  const sigSection = $("sigSection");
  const sigCanvas = $("sigCanvas");
  const sigClearBtn = $("sigClearBtn");
  const customerAgreesChk = $("customerAgreesChk");

  let sigCtx = sigCanvas ? sigCanvas.getContext("2d") : null;

  // ---- State ----
  let lineItems = []; // { serviceCode, serviceText, laborHours, partsPrice, laborRate, notes, estimate }
  let lastEstimate = null; // { req, res }
  let serviceMeta = null;
  let signatureDataUrl = null;
  let editingLineItem = null; // { serviceCode, serviceText }
  let activeLineItemIndex = null;

  // ---- Saved Drafts (localStorage) ----
  const DRAFTS_KEY = "torquemech_drafts_v1";
  const MAX_DRAFTS = 25;

  function getDrafts() {
    try {
      return JSON.parse(localStorage.getItem(DRAFTS_KEY) || "[]");
    } catch {
      return [];
    }
  }

  function setDrafts(arr) {
    localStorage.setItem(DRAFTS_KEY, JSON.stringify(arr));
  }

  function draftLabel(d) {
    return `${d.title} • ${new Date(d.savedAt).toLocaleString()}`;
  }

  function refreshDraftsUI() {
    if (!draftsSelect) return;

    const drafts = getDrafts();
    draftsSelect.innerHTML = `<option value="">— Select a saved draft —</option>` +
      drafts.map(d => `<option value="${d.id}">${draftLabel(d)}</option>`).join("");

    if (draftsMsg) {
      draftsMsg.textContent = drafts.length
        ? `Saved on this device: ${drafts.length}`
        : "No saved drafts yet.";
    }
  }

  function buildDraftTitle() {
    const vehicle = [yearEl?.value, makeEl?.value, modelEl?.value].filter(Boolean).join(" ");
    const servicesCount = lineItems.length;
    return (vehicle || "Estimate") + (servicesCount ? ` (${servicesCount} service${servicesCount > 1 ? "s" : ""})` : "");
  }

  function serializeDraft() {
    return {
      id: String(Date.now()),
      savedAt: Date.now(),
      title: buildDraftTitle(),

      vehicle: {
        year: yearEl?.value || "",
        make: makeEl?.value || "",
        model: modelEl?.value || "",
      },

      // IMPORTANT: storing signature in localStorage can blow quota.
      // We'll intentionally NOT store signature for now (Beta-safe).
      signatureDataUrl: null,

      customer: {
        agrees: !!customerAgreesChk?.checked,
        name: customerNameEl?.value || "",
        phone: customerPhoneEl?.value || "",
        notes: notesEl?.value || "",
      },

      lineItems: Array.isArray(lineItems) ? lineItems : [],
    };
  }

    async function applyDraft(d) {
    if (!d) return;

    // Vehicle (safe restore with model reload)
    if (yearEl) yearEl.value = d.vehicle?.year || "";

    if (makeEl) {
      makeEl.value = d.vehicle?.make || "";
      // Load models immediately (don’t rely on “change” timing)
      await loadModels(makeEl.value);
    }

    if (modelEl) modelEl.value = d.vehicle?.model || "";

    // Customer
    if (customerAgreesChk) customerAgreesChk.checked = !!d.customer?.agrees;
    if (customerNameEl) customerNameEl.value = d.customer?.name || "";
    if (customerPhoneEl) customerPhoneEl.value = d.customer?.phone || "";
    if (notesEl) notesEl.value = d.customer?.notes || "";

    // Restore services
    lineItems = Array.isArray(d.lineItems) ? d.lineItems : [];
    renderLineItems();

    // Reset signature (Beta-safe)
    signatureDataUrl = null;
    clearSignatureCanvas();

    // ✅ IMPORTANT: after loading, let buttons work immediately
    readyForNextService = true;
    updateEstimateButtonState();

    if (draftsMsg) draftsMsg.textContent = `Loaded: ${d.title}`;
  }

  function saveCurrentDraft() {
    const d = serializeDraft();
    const drafts = getDrafts();

    drafts.unshift(d);
    if (drafts.length > MAX_DRAFTS) drafts.length = MAX_DRAFTS;

    setDrafts(drafts);
    refreshDraftsUI();

    if (draftsMsg) draftsMsg.textContent = `Saved: ${d.title}`;
  }

  async function loadSelectedDraft() {
    const id = draftsSelect?.value;
    if (!id) {
      if (draftsMsg) draftsMsg.textContent = "Select a saved draft first.";
      return;
    }

    const drafts = getDrafts();
    const d = drafts.find(x => x.id === id);
    if (!d) {
      if (draftsMsg) draftsMsg.textContent = "Draft not found.";
      return;
    }

    await applyDraft(d);
  }

  function deleteSelectedDraft() {
    const id = draftsSelect?.value;
    if (!id) {
      if (draftsMsg) draftsMsg.textContent = "Select a saved draft first.";
      return;
    }

    const drafts = getDrafts().filter(x => x.id !== id);
    setDrafts(drafts);
    refreshDraftsUI();

    if (draftsMsg) draftsMsg.textContent = "Draft deleted.";
  }

  let laborHoursTouched = false;
  let readyForNextService = true; // ✅ the lock/unlock flag

  // Track if user manually edited labor hours (so we don't overwrite it)
  laborHoursEl?.addEventListener("input", () => {
    laborHoursTouched = true;
  });

  // ---- Utils ----
  function setStatus(kind, msg) {
    if (!statusBox) return;
    statusBox.dataset.kind = kind || "info";
    statusBox.textContent = msg || "";
  }

  function money(n) {
    const x = Number(n || 0);
    return `$${Math.round(x).toLocaleString()}`;
  }

  function fmt1(n) {
    return Number(n).toFixed(1);
  }

  function updateLaborRangeUI() {
    if (!laborHoursRangeEl) return;

    const mn = Number(serviceMeta?.labor_hours_min ?? 0);
    const mx = Number(serviceMeta?.labor_hours_max ?? 0);

    if (mx > 0 && mx >= mn) {
      laborHoursRangeEl.textContent = `Range: ${fmt1(mn)} – ${fmt1(mx)} hrs`;
    } else {
      laborHoursRangeEl.textContent = "";
    }
  }

  async function apiJSON(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      const t = await r.text().catch(() => "");
      throw new Error(`${r.status} ${r.statusText} ${t}`.trim());
    }
    return r.json();
  }

  // ---- OBD → Estimator bridge ----
  function normalizeObdCode(code) {
    return String(code || "").trim().toUpperCase();
  }

  function findOptionValueByText(selectEl, containsText) {
    if (!selectEl) return "";
    const needle = String(containsText || "").toLowerCase();
    for (const opt of Array.from(selectEl.options || [])) {
      const t = (opt.textContent || opt.text || "").toLowerCase();
      if (t.includes(needle)) return opt.value;
    }
    return "";
  }

  async function applyObdFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("obd");
    if (!raw) return;

    const codes = raw
      .split(",")
      .map(c => normalizeObdCode(c))
      .filter(Boolean);

    if (!codes.length) return;

    // ---- Auto-select Diagnostic category (safe assist) ----
    try {
      if (categoryEl && !categoryEl.value) {
        const diagValue =
          findOptionValueByText(categoryEl, "diagn") ||
          findOptionValueByText(categoryEl, "inspection") ||
          findOptionValueByText(categoryEl, "engine");

        if (diagValue) {
          categoryEl.value = diagValue;

          // ✅ IMPORTANT: trigger normal flow
          categoryEl.dispatchEvent(new Event("change"));

          setStatus("info", "Diagnostic category auto-selected based on OBD codes.");
        }
      }
    }
    catch (e) {
      console.warn("OBD category assist failed:", e);
    }

    // Create / reuse a small "OBD loaded" card (optional but nice)
    let card = document.getElementById("obdBridgeCard");
    if (!card) {
      card = document.createElement("div");
      card.id = "obdBridgeCard";
      card.className = "tm-card";
      card.style.marginTop = "12px";
      card.style.padding = "14px";
      card.style.border = "1px solid rgba(255,255,255,.08)";

      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap;">
          <div>
            <div style="font-weight:800; font-size:16px;">OBD Codes Loaded</div>
            <div id="obdBridgeTitle" style="margin-top:6px; font-size:14px; opacity:.95;"></div>
            <div id="obdBridgeDesc" style="margin-top:6px; font-size:13px; opacity:.75;"></div>
          </div>
        </div>
      `;

      // Put it near Notes if possible (safe)
      const notesRow = document.getElementById("notesEl")?.closest?.(".tm-field") || null;
      if (notesRow && notesRow.parentElement) {
        notesRow.parentElement.insertBefore(card, notesRow);
      } else {
        // fallback: append to page
        document.body.appendChild(card);
      }
    }

    const titleEl = document.getElementById("obdBridgeTitle");
    const descEl = document.getElementById("obdBridgeDesc");

    if (titleEl) titleEl.textContent = codes.join(", ");
    if (descEl) descEl.textContent = "Added to Notes (you can edit/remove).";

    // Prefill Notes (append, don’t overwrite)
    if (notesEl) {
      const existing = (notesEl.value || "").trim();
      const block = codes.map(c => `OBD: ${c}`).join("\n");
      notesEl.value = existing ? `${existing}\n\n${block}` : block;
    }

    // Optional: prevent re-adding on refresh/back
    // history.replaceState({}, "", window.location.pathname);
  }

    // Wire buttons
    const addBtn = document.getElementById("obdAddDiagBtn");
    const dismissBtn = document.getElementById("obdClearParamBtn");

    addBtn?.addEventListener("click", async () => {
      try {
        // Find Diagnostics category (best-effort)
        const diagCatValue =
          findOptionValueByText(categoryEl, "diagn") ||
          findOptionValueByText(categoryEl, "inspection") ||
          findOptionValueByText(categoryEl, "engine");

        if (diagCatValue) {
          categoryEl.value = diagCatValue;
          await loadServices(categoryEl.value);

          // Find a diagnostic-ish service (best-effort)
          const diagSvcValue =
            findOptionValueByText(serviceEl, "diagn") ||
            findOptionValueByText(serviceEl, "inspection") ||
            findOptionValueByText(serviceEl, "scan") ||
            findOptionValueByText(serviceEl, "check");

          if (diagSvcValue) {
            serviceEl.value = diagSvcValue;
            await loadServiceMeta(serviceEl.value);
            updateEstimateButtonState();
            setStatus("ok", `OBD ${code} attached. Diagnostic service selected.`);
            return;
          }
        }

        // Fallback if we can’t auto-map
        updateEstimateButtonState();
        setStatus("info", `OBD ${code} attached to Notes. Select a diagnostic service to estimate.`);
      } catch (e) {
        setStatus("error", `OBD bridge failed: ${e.message}`);
      }
    });

    dismissBtn?.addEventListener("click", () => {
      // Remove query param so refresh doesn’t re-run it
      const url = new URL(window.location.href);
      url.searchParams.delete("obd");
      window.history.replaceState({}, "", url.toString());

      // Hide card
      const c = document.getElementById("obdBridgeCard");
      if (c) c.style.display = "none";
      setStatus("info", "OBD card dismissed.");
    });

  // Saved drafts (local)
  const saveDraftBtn = $("saveDraftBtn");
  const draftsSelect = $("draftsSelect");
  const loadDraftBtn = $("loadDraftBtn");
  const deleteDraftBtn = $("deleteDraftBtn");
  const draftsMsg = $("draftsMsg");

  // ---- Years / Makes / Models ----
  function populateYears() {
    if (!yearEl) return;

    const currentYear = new Date().getFullYear();
    const startYear = currentYear;
    const endYear = currentYear - 30;

    yearEl.innerHTML = `<option value="">Select Year</option>`;
    for (let y = startYear; y >= endYear; y--) {
      const opt = document.createElement("option");
      opt.value = String(y);
      opt.textContent = String(y);
      yearEl.appendChild(opt);
    }

    yearEl.value = String(currentYear);
  }

  async function loadMakes() {
    if (!makeEl) return;
    makeEl.innerHTML = `<option value="">Select make…</option>`;
    const makes = await apiJSON("/api/makes");
    for (const m of makes) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      makeEl.appendChild(opt);
    }
  }

  async function loadModels(make) {
    if (!modelEl) return;

    modelEl.innerHTML = `<option value="">Select model…</option>`;
    if (!make) return;

    let models;
    try {
      models = await apiJSON(`/api/models/${encodeURIComponent(make)}`);
    } catch (_) {
      models = await apiJSON(`/api/models?make=${encodeURIComponent(make)}`);
    }

    modelEl.innerHTML = `<option value="">Select model…</option>`;
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      modelEl.appendChild(opt);
    }
  }

  // ---- Categories / services ----
  async function loadCategories() {
    if (!categoryEl) return;
    categoryEl.innerHTML = `<option value="">Select category…</option>`;
    const cats = await apiJSON("/api/categories");
    for (const c of cats) {
      const opt = document.createElement("option");
      opt.value = c.key;
      opt.textContent = c.name;
      categoryEl.appendChild(opt);
    }
  }

  async function loadServices(categoryKey) {
    if (!serviceEl) return;

    serviceEl.innerHTML = `<option value="">Select service…</option>`;
    serviceMeta = null;
    laborHoursTouched = false;

    if (!categoryKey) return;

    const svcs = await apiJSON(`/api/services/${encodeURIComponent(categoryKey)}`);
    for (const s of svcs) {
      const opt = document.createElement("option");
      opt.value = s.code || "";
      opt.textContent = s.name || s.code || "Service";
      serviceEl.appendChild(opt);
    }
  }

  async function loadServiceMeta(serviceCode) {
    serviceMeta = null;

    if (!serviceCode) {
      if (laborHoursRangeEl) laborHoursRangeEl.textContent = "";
      return;
    }

    serviceMeta = await apiJSON(`/api/service/${encodeURIComponent(serviceCode)}`);

    updateLaborRangeUI();

    const mn = Number(serviceMeta?.labor_hours_min ?? 0);
    const mx = Number(serviceMeta?.labor_hours_max ?? 0);
    const midpoint = mx > 0 && mx >= mn ? (mn + mx) / 2 : 0;

    if (!laborHoursTouched && midpoint > 0 && laborHoursEl) {
      laborHoursEl.value = fmt1(midpoint);
    }
  }

  // ---- Signature / modal ----
  function getWantSig() {
    const checked = document.querySelector('input[name="wantSig"]:checked');
    return checked ? checked.value : "yes";
  }

  function setSigVisible(visible) {
    if (!sigSection) return;
    sigSection.classList.toggle("hidden", !visible);
    if (!visible) {
      signatureDataUrl = null;
      clearSignatureCanvas();
    }
  }

  function openConfirm() {
    if (!confirmModal) return;

    if (confirmMsg) confirmMsg.textContent = "";
    confirmModal.classList.remove("hidden");
    confirmModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");

    // show/hide signature section depending on radio choice
    setSigVisible(getWantSig() === "yes");

    // ✅ STEP 2 — render services + grand total
    const listEl = document.getElementById("confirmServicesList");
    if (listEl) {
      const total = lineItems.reduce((sum, it) => sum + Number(it.estimate || 0), 0);

      listEl.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:8px;">
          ${lineItems.map(it => `
            <div style="display:flex;justify-content:space-between;">
              <div>${it.serviceText}</div>
              <div style="font-weight:700">${money(it.estimate)}</div>
            </div>
          `).join("")}
          <div style="border-top:1px solid rgba(255,255,255,.15);padding-top:8px;display:flex;justify-content:space-between;">
            <div style="font-weight:800;">Grand Total</div>
            <div style="font-weight:900;">${money(total)}</div>
          </div>
        </div>
      `;
    }

    // make sure canvas is sized correctly when modal opens
    resizeSigCanvas();
  }
  function closeConfirm() {
    confirmModal?.classList.add("hidden");
    confirmModal?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    if (confirmMsg) confirmMsg.textContent = "";
  }

  confirmBackdrop?.addEventListener("click", closeConfirm);
  confirmCloseBtn?.addEventListener("click", closeConfirm);
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && confirmModal && !confirmModal.classList.contains("hidden")) closeConfirm();
  });

  document.querySelectorAll('input[name="wantSig"]').forEach((r) => {
    r.addEventListener("change", () => setSigVisible(getWantSig() === "yes"));
  });

 // ---- Signature pad ----
  let isDrawing = false;
  let lastX = 0, lastY = 0;

  function resizeSigCanvas() {
    if (!sigCanvas) return;

    const rect = sigCanvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    // Preserve current drawing while resizing
    const prev = sigCanvas.toDataURL("image/png");

    // Resize backing store
    sigCanvas.width = Math.max(1, Math.floor(rect.width * dpr));
    sigCanvas.height = Math.max(1, Math.floor(rect.height * dpr));

    // Re-grab context after resize
    sigCtx = sigCanvas.getContext("2d");
    sigCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Subtle pad background so white ink pops
    sigCtx.clearRect(0, 0, rect.width, rect.height);
    sigCtx.fillStyle = "rgba(255,255,255,0.06)";
    sigCtx.fillRect(0, 0, rect.width, rect.height);

    // ✅ White ink
    sigCtx.lineWidth = 3.5;
    sigCtx.lineCap = "round";
    sigCtx.lineJoin = "round";
    sigCtx.strokeStyle = "#ffffff";

    // Restore previous drawing
    const img = new Image();
    img.onload = () => {
      sigCtx.drawImage(img, 0, 0, rect.width, rect.height);
    };
    img.src = prev;
  }

  function clearSignatureCanvas() {
    if (!sigCanvas) return;
    const rect = sigCanvas.getBoundingClientRect();
    const ctx = sigCanvas.getContext("2d");
    ctx.clearRect(0, 0, sigCanvas.width, sigCanvas.height);

    // re-paint subtle background after clear
    ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    ctx.fillRect(0, 0, rect.width, rect.height);
  }

  function canvasIsBlank() {
    if (!sigCanvas) return true;
    const ctx = sigCanvas.getContext("2d");
    const pixels = ctx.getImageData(0, 0, sigCanvas.width, sigCanvas.height).data;
    for (let i = 3; i < pixels.length; i += 4) {
      if (pixels[i] !== 0) return false;
    }
    return true;
  }

  function getCanvasPos(e) {
    const rect = sigCanvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return { x: clientX - rect.left, y: clientY - rect.top };
  }

  function startDraw(e) {
    if (!sigCanvas || !sigCtx) return;
    isDrawing = true;
    const p = getCanvasPos(e);
    lastX = p.x; lastY = p.y;
  }

  function draw(e) {
    if (!isDrawing || !sigCtx) return;
    e.preventDefault();

    const p = getCanvasPos(e);
    sigCtx.beginPath();
    sigCtx.moveTo(lastX, lastY);
    sigCtx.lineTo(p.x, p.y);
    sigCtx.stroke();
    lastX = p.x; lastY = p.y;
  }

  function endDraw() { isDrawing = false; }

  if (sigCanvas) {
    // If modal opens, you already call resizeSigCanvas() — good.
    // This keeps it responsive if window changes while modal is open.
    window.addEventListener("resize", () => {
      if (confirmModal && !confirmModal.classList.contains("hidden") && getWantSig() === "yes") {
        resizeSigCanvas();
      }
    });

    sigCanvas.addEventListener("mousedown", startDraw);
    sigCanvas.addEventListener("mousemove", draw);
    window.addEventListener("mouseup", endDraw);

    sigCanvas.addEventListener("touchstart", startDraw, { passive: false });
    sigCanvas.addEventListener("touchmove", draw, { passive: false });
    window.addEventListener("touchend", endDraw);

    sigClearBtn?.addEventListener("click", () => {
      signatureDataUrl = null;
      clearSignatureCanvas();
    });
  }
  // ---- Build request ----
  function buildRequest(extra = {}) {
    return {
      year: Number(yearEl?.value),
      make: (makeEl?.value || "").trim(),
      model: (modelEl?.value || "").trim(),
      category: (categoryEl?.value || "").trim() || null,
      serviceCode: (serviceEl?.value || "").trim() || null,

      laborHours: Number(laborHoursEl?.value || 0),
      partsPrice: Number(partsPriceEl?.value || 0),
      laborRate: Number(laborRateEl?.value || 0),

      notes: (notesEl?.value || "").trim() || null,
      customerName: (customerNameEl?.value || "").trim() || null,
      customerPhone: (customerPhoneEl?.value || "").trim() || null,

      customerAgrees: !!(customerAgreesChk?.checked),
      signatureDataUrl: null,

      zip: "00000", // placeholder for now
      ...extra,
    };
  }

  // ----- Line Items UI (Service cards) -----
  function renderLineItems() {
    if (!lineItemsWrap || !lineItemsList) return;

    lineItemsWrap.classList.toggle("hidden", lineItems.length === 0);

    lineItemsList.innerHTML = lineItems
      .map((it, idx) => {
        const est = it.estimate != null ? money(it.estimate) : "—";

        return `
        <div class="line-item selectable" data-idx="${idx}">
          <div>
            <div class="name">${it.serviceText || "Service"}</div>
            <div class="meta">
              Labor: ${Number(it.laborHours || 0).toFixed(1)}h •
              Rate: $${Number(it.laborRate || 0).toFixed(0)}/hr •
              Parts: ${money(it.partsPrice || 0)}
            </div>
            <div class="money">Estimate: ${est}</div>
          </div>

          <div class="line-actions">
            <button type="button" class="ghost" data-action="estimate">Recalculate</button>
            <button type="button" class="remove" data-action="remove">Remove</button>
          </div>
        </div>
      `;
      })
      .join("");

    updateEstimateButtonState();
  }

  function updateEstimateButtonState() {
    if (!estimateBtn) return;

    const hasBasics = !!(yearEl?.value && makeEl?.value && modelEl?.value);
    const hasSelection = !!(categoryEl?.value && serviceEl?.value);

    // --- Add Service button label (dynamic) ---
    if (addLineBtn) {
      // If user already added a service (locked state), guide them to add another
      addLineBtn.textContent = readyForNextService ? "+ Add Service" : "+ Add Another Service";
    }

    estimateBtn.disabled = !(hasBasics && hasSelection && readyForNextService);


// Hint text: when locked, explain the flow
if (addServiceHint) addServiceHint.hidden = !!readyForNextService; // show only after a service is added
if (getEstimateHint) {
  // Show hint only when button is disabled
  getEstimateHint.hidden = !estimateBtn.disabled;

  if (!getEstimateHint.hidden) {
    getEstimateHint.textContent = !hasBasics
      ? "Select year, make, and model first."
      : !hasSelection
        ? "Select a category and service first."
        : "To add another service, click “+ Add Another Service”, then choose the next service.";
  }
}

    // + Add Service enabled ONLY after a service has been added
    if (addLineBtn) addLineBtn.disabled = readyForNextService;

    // keep status helpful, but don't spam over error messages
    if (!hasBasics) setStatus("info", "Select year, make, and model.");
    else if (!hasSelection) setStatus("info", "Select a category and service.");
    else if (!readyForNextService) setStatus("info", "Click + Add Service to add another service.");
    else setStatus("info", "Click Get Estimate to add this service.");
  }

  // ---- Get Estimate FIRST ----
  estimateBtn?.addEventListener("click", async () => {
    if (!readyForNextService) return;

    if (!(yearEl.value && makeEl.value && modelEl.value)) {
      setStatus("error", "Select year, make, and model first.");
      return;
    }
    if (!editingLineItem) {
      if (!categoryEl.value) {
        setStatus("error", "Select a category.");
        return;
      }
      if (!serviceEl.value) {
        setStatus("error", "Select a service.");
        return;
      }
    }

    // lock immediately so user can’t spam add
    readyForNextService = false;
    updateEstimateButtonState();

    const serviceText = editingLineItem
      ? (editingLineItem.serviceText || editingLineItem.serviceCode)
      : (serviceEl.options[serviceEl.selectedIndex]?.textContent?.trim() || serviceEl.value);

    const it = {
      serviceCode: editingLineItem ? editingLineItem.serviceCode : serviceEl.value,
      serviceText,
      laborHours: Number(laborHoursEl.value || 0),
      partsPrice: Number(partsPriceEl.value || 0),
      laborRate: Number(laborRateEl.value || 0),
      notes: (notesEl?.value || "").trim() || null,
      estimate: null,
    };

    // add the card immediately
    lineItems.push(it);
    activeLineItemIndex = lineItems.length - 1;
    renderLineItems();

    setStatus("info", `Pricing: ${serviceText}…`);

    try {
      const req = buildRequest({
        serviceCode: it.serviceCode,
        laborHours: it.laborHours,
        partsPrice: it.partsPrice,
        laborRate: it.laborRate,
        notes: it.notes,
      });

      const res = await apiJSON("/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
      });

      it.estimate = res.estimate ?? null;
      editingLineItem = null;
      lastEstimate = { req, res };

      renderLineItems();
      setStatus("ok", `${it.serviceText}: ${money(it.estimate)} — click + Add Service to enter another service.`);

      // stay locked until + Add Service
      readyForNextService = false;
      updateEstimateButtonState();
    } catch (e) {
      // if estimate fails, remove the just-added item and unlock
      lineItems.pop();
      renderLineItems();

      readyForNextService = true;
      updateEstimateButtonState();

      setStatus("error", `Estimate failed: ${e.message}`);
    }
  });

  // + Add Service
  addLineBtn?.addEventListener("click", () => {
    // Clear selections
    categoryEl.value = "";
    serviceEl.value = "";
    serviceEl.innerHTML = `<option value="">Select service…</option>`;

    serviceMeta = null;
    if (laborHoursRangeEl) laborHoursRangeEl.textContent = "";

    laborHoursTouched = false;
    laborHoursEl.value = "0";
    partsPriceEl.value = "0";
    if (notesEl) notesEl.value = "";

    // 🔓 Unlock Get Estimate
    activeLineItemIndex = null;
    readyForNextService = true;
    updateEstimateButtonState();

    setStatus("info", "Ready for next service.");
  });

  // IMPORTANT: this must be async because we use await inside
  lineItemsList?.addEventListener("click", async (e) => {
    const btn = e.target?.closest?.("button[data-action]");
    if (!btn) return;

    const row = btn.closest(".line-item");
    const idx = Number(row?.dataset?.idx);
    if (!Number.isFinite(idx)) return;

    const action = btn.dataset.action;
    const it = lineItems[idx];
    if (!it) return;

    // --- PER-SERVICE ESTIMATE ---
    if (action === "estimate") {
      if (!(yearEl.value && makeEl.value && modelEl.value)) {
        setStatus("error", "Select year, make, and model first.");
        return;
      }

      // Load this card's values into the shared inputs when switching cards
      if (activeLineItemIndex !== idx) {
        activeLineItemIndex = idx;

        if (laborHoursEl) laborHoursEl.value = String(it.laborHours ?? 0);
        if (partsPriceEl) partsPriceEl.value = String(it.partsPrice ?? 0);
        if (laborRateEl) laborRateEl.value = String(it.laborRate ?? 0);
        if (notesEl) notesEl.value = it.notes || "";
      }
      // Second click on same card: apply edited shared inputs back to THIS card only
      const currentHours = Number(laborHoursEl?.value || 0);
      if (Number.isFinite(currentHours) && currentHours >= 0) it.laborHours = currentHours;

      const currentParts = Number(partsPriceEl?.value || 0);
      if (Number.isFinite(currentParts) && currentParts >= 0) it.partsPrice = currentParts;

      const currentRate = Number(laborRateEl?.value || 0);
      if (Number.isFinite(currentRate) && currentRate > 0) it.laborRate = currentRate;

      if (notesEl) it.notes = (notesEl.value || "").trim() || null;

      renderLineItems();
      setStatus("info", `Pricing: ${it.serviceText}…`);

      try {
        const req = buildRequest({
          serviceCode: it.serviceCode,
          laborHours: it.laborHours,
          partsPrice: it.partsPrice,
          laborRate: it.laborRate,
          notes: it.notes,
        });

        const res = await apiJSON("/estimate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(req),
        });

        it.estimate = res.estimate ?? null;
        renderLineItems();
        setStatus("ok", `${it.serviceText}: ${money(it.estimate)}`);

        readyForNextService = false;
        updateEstimateButtonState();
      } catch (err) {
        setStatus("error", `Estimate failed: ${err.message}`);
      }
      return;
    }

    // --- REMOVE ---
    if (action === "remove") {
      activeLineItemIndex = null;
      lineItems.splice(idx, 1);
      renderLineItems();
      return;
    }

  });

  // Confirm Add = finalize signature and generate PDF
  confirmAddBtn?.addEventListener("click", async () => {
    if (confirmMsg) confirmMsg.textContent = "";

    try {
      if (!lineItems.length) {
        if (confirmMsg) confirmMsg.textContent = "Add at least one service first.";
        return;
      }

      const wantSig = getWantSig();

      // Signature required?
      if (wantSig === "yes") {
        if (!sigCanvas || canvasIsBlank()) {
          if (confirmMsg) confirmMsg.textContent = "Signature is required. Please sign or choose 'No signature'.";
          return;
        }
        signatureDataUrl = sigCanvas.toDataURL("image/png");
      } else {
        signatureDataUrl = null;
      }

      // Multi-line PDF uses lineItems; no need to call /estimate here.
      if (!lineItems.length) {
        if (confirmMsg) confirmMsg.textContent = "Add at least one service first.";
        return;
      }
      const missing = lineItems.some(it => it.estimate == null);
      if (missing) {
        if (confirmMsg) confirmMsg.textContent = "Some services are missing prices. Click Generate All first.";
        return;
      }
      // Generate PDF
      setStatus("info", "Generating PDF…");

      const pdfResponse = await fetch("/estimate/pdf_multi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          year: Number(yearEl.value),
          make: makeEl.value,
          model: modelEl.value,
          notes: (notesEl?.value || "").trim() || null,
          customerName: (customerNameEl?.value || "").trim() || null,
          customerPhone: (customerPhoneEl?.value || "").trim() || null,
          customerAgrees: !!customerAgreesChk?.checked,
          signatureDataUrl,
          lineItems: lineItems.map((it) => ({
            serviceCode: it.serviceCode,
            serviceText: it.serviceText,
            laborHours: Number(it.laborHours || 0),
            partsPrice: Number(it.partsPrice || 0),
            laborRate: Number(it.laborRate || 0),
            estimate: it.estimate != null ? Number(it.estimate) : null,
          })),
        }),
      });

      const contentType = (pdfResponse.headers.get("content-type") || "").toLowerCase();

      if (!contentType.includes("application/pdf")) {
        const text = await pdfResponse.text().catch(() => "");
        throw new Error(`Expected PDF, got "${contentType}". Body: ${text.slice(0, 300)}`);
      }

      if (!pdfResponse.ok) {
        const t = await pdfResponse.text().catch(() => "");
        throw new Error(`${pdfResponse.status} ${pdfResponse.statusText} ${t}`.trim());
      }

      const pdfBlob = await pdfResponse.blob();
      const pdfUrl = URL.createObjectURL(pdfBlob);

      // Try download
      const a = document.createElement("a");
      a.href = pdfUrl;
      a.download = "torquemech_estimate.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();

      setStatus("ok", "PDF ready.");

      if (confirmMsg) {
        confirmMsg.innerHTML = `
          PDF ready.<br>
          <a href="${pdfUrl}" download="torquemech_estimate.pdf">Download PDF</a>
          &nbsp;|&nbsp;
          <a href="${pdfUrl}" target="_blank" rel="noopener">Open PDF</a>
        `;
      }

      // Do NOT auto-close immediately
      setTimeout(() => URL.revokeObjectURL(pdfUrl), 60000);

      setStatus("ok", "PDF downloaded.");
      closeConfirm();

    } catch (e) {
      setStatus("error", `PDF failed: ${e.message}`);
      if (confirmMsg) confirmMsg.textContent = `PDF failed: ${e.message}`;
    }
  });

  // ---- Generate All Service Estimates ----
  generateAllBtn?.addEventListener("click", async () => {
    if (!lineItems.length) {
      setStatus("error", "Add at least one service using Get Estimate first.");
      return;
    }
    if (!(yearEl.value && makeEl.value && modelEl.value)) {
      setStatus("error", "Select year, make, and model first.");
      return;
    }

    setStatus("info", "Generating all service estimates…");

    try {
      // Recalculate each line item one-by-one
      for (const it of lineItems) {
        const req = buildRequest({
          serviceCode: it.serviceCode,
          laborHours: it.laborHours,
          partsPrice: it.partsPrice,
          laborRate: it.laborRate,
          notes: it.notes,
        });

        const res = await apiJSON("/estimate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(req),
        });

        it.estimate = res.estimate ?? null;
        lastEstimate = { req, res }; // keeps PDF working with “latest”
        renderLineItems();
      }

      setStatus("ok", "All service estimates generated.");
      openConfirm();
    } catch (e) {
      setStatus("error", `Generate all failed: ${e.message}`);
    }
  });

  // ---- Clear fields (Hard reset) ----
  clearBtn?.addEventListener("click", async () => {
    try {
      closeConfirm();
    } catch (_) {}

    activeLineItemIndex = null;

    // ---- State reset (DO NOT redeclare) ----
    lineItems = [];
    lastEstimate = null;
    serviceMeta = null;
    signatureDataUrl = null;
    laborHoursTouched = false;
    readyForNextService = true;
    if (laborHoursRangeEl) laborHoursRangeEl.textContent = "";

    // VIN
    if (vinEl) vinEl.value = "";
    vinPanel?.classList.add("hidden");

    // vehicle
    const now = new Date().getFullYear();
    if (yearEl) yearEl.value = String(now);
    if (makeEl) makeEl.value = "";
    if (modelEl) modelEl.innerHTML = `<option value="">Select model…</option>`;

    // service
    if (categoryEl) categoryEl.value = "";
    if (serviceEl) serviceEl.innerHTML = `<option value="">Select service…</option>`;

    // inputs
    if (laborHoursEl) laborHoursEl.value = "0";
    if (partsPriceEl) partsPriceEl.value = "0";
    if (laborRateEl) laborRateEl.value = "90";
    if (notesEl) notesEl.value = "";
    if (customerNameEl) customerNameEl.value = "";
    if (customerPhoneEl) customerPhoneEl.value = "";

    // signature
    const wantYes = document.querySelector('input[name="wantSig"][value="yes"]');
    if (wantYes) wantYes.checked = true;
    if (customerAgreesChk) customerAgreesChk.checked = true;
    setSigVisible(false);
    clearSignatureCanvas();

    // UI blocks
    if (lineItemsList) lineItemsList.innerHTML = "";
    lineItemsWrap?.classList.add("hidden");

    estimatePreview?.classList.add("hidden");
    if (previewTotalText) previewTotalText.textContent = "—";
    if (previewSubText) previewSubText.textContent = "";

    // reload core dropdown data
    try {
      await loadMakes();
      await loadCategories();
      
      await applyObdFromQuery();
    } catch (_) {}

    updateEstimateButtonState();
    setStatus("info", "Cleared. Start a new estimate.");
  });

  // ---- VIN toggle ----
  vinToggle?.addEventListener("click", () => {
    vinPanel?.classList.toggle("hidden");
    vinToggle.classList.toggle("expanded");
  });

  // ---- VIN decode ----
  vinLookupBtn?.addEventListener("click", async () => {
    const vin = (vinEl?.value || "").trim().toUpperCase();

    if (vin.length !== 17) {
      setStatus("error", "VIN must be 17 characters.");
      return;
    }

    setStatus("info", "Decoding VIN…");

    try {
      const res = await apiJSON(`/api/vin/${encodeURIComponent(vin)}`);

      yearEl.value = String(res.year);
      // Select make
      const vinMake = (res.make || "").trim().toLowerCase();
      let makeIndex = -1;
      for (let i = 0; i < makeEl.options.length; i++) {
        const t = (makeEl.options[i].text || "").trim().toLowerCase();
        const v = (makeEl.options[i].value || "").trim().toLowerCase();
        if (t === vinMake || v === vinMake) {
          makeIndex = i;
          break;
        }
      }
      if (makeIndex < 0) {
        setStatus("warn", `VIN decoded, but make "${res.make}" not found.`);
        return;
      }
      makeEl.selectedIndex = makeIndex;

      // Load models
      await loadModels(makeEl.value || makeEl.options[makeIndex].text);

      // Select model
      const vinModel = (res.model || "").trim().toLowerCase();
      let modelIndex = -1;
      for (let i = 0; i < modelEl.options.length; i++) {
        const t = (modelEl.options[i].text || "").trim().toLowerCase();
        const v = (modelEl.options[i].value || "").trim().toLowerCase();
        if (t === vinModel || v === vinModel) {
          modelIndex = i;
          break;
        }
      }
      if (modelIndex >= 0) modelEl.selectedIndex = modelIndex;

      updateEstimateButtonState();
      setStatus("ok", "VIN decoded and vehicle filled.");
    } catch (e) {
      setStatus("error", `VIN lookup failed: ${e.message}`);
    }
  });

  // Allow Enter key in VIN field to trigger Decode VIN
  vinEl?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      vinLookupBtn?.click();
    }
  });

  makeEl?.addEventListener("change", async () => {
    try {
      modelEl.innerHTML = `<option value="">Select model…</option>`;
      modelEl.value = "";
      await loadModels(makeEl.value);
      updateEstimateButtonState();
    } catch (e) {
      setStatus("error", `Models failed: ${e.message}`);
    }
  });

  categoryEl?.addEventListener("change", async () => {
    try {
      await loadServices(categoryEl.value);
      updateEstimateButtonState();
    } catch (e) {
      setStatus("error", `Services failed: ${e.message}`);
    }
  });

  serviceEl?.addEventListener("change", async () => {
    try {
      await loadServiceMeta(serviceEl.value);
      updateEstimateButtonState();
    } catch (e) {
      setStatus("error", `Service detail failed: ${e.message}`);
    }
  });

  // ---- PWA install (optional) ----
  if (installBtn) installBtn.style.display = "none";
  let deferredPrompt = null;

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    if (installBtn) installBtn.style.display = "";
  });

  installBtn?.addEventListener("click", async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    if (installBtn) installBtn.style.display = "none";
  });

  // ---- Draft button wiring ----
  saveDraftBtn?.addEventListener("click", saveCurrentDraft);
  loadDraftBtn?.addEventListener("click", async () => {
    await loadSelectedDraft();
  });
  deleteDraftBtn?.addEventListener("click", deleteSelectedDraft);

  // Populate dropdown on page load
  refreshDraftsUI();

  // ---- Init ----
  (async () => {
    try {
      populateYears();
      await loadMakes();
      await loadCategories();
      await applyObdFromQuery();

      if (modelEl) modelEl.innerHTML = `<option value="">Select model…</option>`;
      if (serviceEl) serviceEl.innerHTML = `<option value="">Select service…</option>`;

      updateEstimateButtonState();
      setStatus("info", "Select a service and click Get Estimate. To add another one, click + Add Service.");
    } catch (e) {
      setStatus("error", `Init failed: ${e.message}`);
    }
  })();
})();

