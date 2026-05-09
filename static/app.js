/* 🔒 LOCKED (Beta Stabilization)
   Draft load / Generate All first-click fix is working.
   Do NOT edit draft functions unless absolutely necessary.
   If changes are needed, edit app.locked.js first and diff carefully.
*/
// static/app.js — CLEAN (Beta-stable)
(() => {
  function trackClarity(eventName, data) {
    try {
      if (typeof window.tmTrackClarity === "function") {
        window.tmTrackClarity(eventName, data || {});
      } else if (typeof window.clarity === "function") {
        window.clarity("event", eventName, data || {});
      }
    } catch (_) {}
  }

  let searchDebounceTimer = null;

  async function vehicleUiApiJSON(url, opts) {
    const response = await fetch(url, opts);
    if (!response.ok) {
      let detail = "";
      try {
        detail = await response.text();
      } catch (_) {}
      throw new Error(detail || `${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  async function initSharedVehicleSelector({
    yearSelect,
    makeSelect,
    makeSearch,
    makeResults,
    modelSelect,
    clearButton,
    initialVehicle = {},
    startYear = 1980,
    onChange,
    onModelLoadingChange,
  } = {}) {
    if (!yearSelect || !makeSelect || !makeSearch || !makeResults || !modelSelect) {
      return null;
    }

    const vehicle = {
      year: String(initialVehicle.year || ""),
      make: String(initialVehicle.make || ""),
      model: String(initialVehicle.model || ""),
    };

    const notifyChange = () => {
      if (typeof onChange === "function") {
        onChange({
          year: vehicle.year,
          make: vehicle.make,
          model: vehicle.model,
          hasSelection: Boolean(vehicle.year || vehicle.make || vehicle.model),
          isComplete: Boolean(vehicle.year && vehicle.make && vehicle.model),
        });
      }
    };

    const setModelLoading = (isLoading) => {
      if (typeof onModelLoadingChange === "function") {
        onModelLoadingChange(Boolean(isLoading), { ...vehicle });
      }
    };

    let makes = [];
    let models = [];
    const currentYear = new Date().getFullYear();

    let modelSearch = modelSelect.parentElement?.querySelector(".vehicle-model-search");
    if (!modelSearch) {
      modelSearch = document.createElement("input");
      modelSearch.type = "text";
      modelSearch.className = "vehicle-model-search";
      modelSearch.autocomplete = "off";
      modelSelect.insertAdjacentElement("beforebegin", modelSearch);
    }
    modelSearch.placeholder = "Search model...";
    modelSearch.disabled = true;

    let modelResults = modelSelect.parentElement?.querySelector(".vehicle-model-results");
    if (!modelResults) {
      modelResults = document.createElement("div");
      modelResults.className = "vehicle-model-results";
      modelSearch.insertAdjacentElement("afterend", modelResults);
    }
    modelResults.style.display = "none";
    modelResults.style.marginTop = "6px";
    modelResults.style.border = "1px solid rgba(255,255,255,.12)";
    modelResults.style.borderRadius = "12px";
    modelResults.style.overflowY = "auto";

    modelSelect.style.display = "none";

    yearSelect.innerHTML = `<option value="">Select Year</option>`;
    for (let year = currentYear; year >= startYear; year--) {
      const option = document.createElement("option");
      option.value = String(year);
      option.textContent = String(year);
      yearSelect.appendChild(option);
    }
    yearSelect.value = vehicle.year;

    const hideMakeResults = () => {
      makeResults.style.display = "none";
      makeResults.innerHTML = "";
    };

    const hideModelResults = () => {
      modelResults.style.display = "none";
      modelResults.innerHTML = "";
    };

    const normalizeModelSearch = (value) =>
      String(value || "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]/g, "");

    const renderMakeResults = (query) => {
      const normalizedQuery = query.trim().toLowerCase();

      if (!normalizedQuery) {
        hideMakeResults();
        return;
      }

      const filtered = makes
        .filter((make) => make.toLowerCase().includes(normalizedQuery))
        .slice(0, 8);

      if (!filtered.length) {
        hideMakeResults();
        return;
      }

      makeResults.innerHTML = filtered
        .map(
          (make) => `
            <button
              type="button"
              class="make-result-item"
              data-make="${make}"
              style="
                display:block;
                width:100%;
                text-align:left;
                padding:12px 14px;
                background:#ffffff;
                color:#0f172a;
                border:none;
                border-bottom:1px solid #e5e7eb;
                cursor:pointer;
                font-size:16px;
              "
            >${make}</button>
          `
        )
        .join("");

      makeResults.style.display = "block";
    };

    const populateMakeOptions = () => {
      makeSelect.innerHTML = `<option value="">Select make...</option>`;
      makes.forEach((make) => {
        const option = document.createElement("option");
        option.value = make;
        option.textContent = make;
        makeSelect.appendChild(option);
      });
      makeSelect.value = vehicle.make;
      makeSearch.value = vehicle.make;
    };

    const renderModelResults = (query) => {
      if (modelSearch.disabled) {
        hideModelResults();
        return;
      }

      const normalizedQuery = normalizeModelSearch(query);

      if (!normalizedQuery) {
        hideModelResults();
        return;
      }

      const filtered = models
        .filter((model) => normalizeModelSearch(model).includes(normalizedQuery))
        .slice(0, 8);

      if (!filtered.length) {
        hideModelResults();
        return;
      }

      modelResults.innerHTML = filtered
        .map(
          (model) => `
            <button
              type="button"
              class="model-result-item"
              data-model="${model}"
              style="
                display:block;
                width:100%;
                text-align:left;
                padding:12px 14px;
                background:#ffffff;
                color:#0f172a;
                border:none;
                border-bottom:1px solid #e5e7eb;
                cursor:pointer;
                font-size:16px;
              "
            >${model}</button>
          `
        )
        .join("");

      modelResults.style.display = "block";
    };

    const populateModels = async (selectedMake, selectedModel = "") => {
      models = [];
      modelSearch.value = "";
      modelSearch.disabled = true;
      hideModelResults();
      modelSelect.innerHTML = `<option value="">Loading models...</option>`;
      modelSelect.disabled = true;
      setModelLoading(true);

      if (!selectedMake) {
        modelSelect.innerHTML = `<option value="">Select model...</option>`;
        modelSearch.placeholder = "Select make first...";
        setModelLoading(false);
        return;
      }

      try {
        models = await vehicleUiApiJSON(`/api/models/${encodeURIComponent(selectedMake)}`);
        modelSelect.innerHTML = `<option value="">Select model...</option>`;

        models.forEach((model) => {
          const option = document.createElement("option");
          option.value = model;
          option.textContent = model;
          modelSelect.appendChild(option);
        });

        modelSelect.disabled = false;
        modelSelect.value = selectedModel;
        modelSearch.disabled = false;
        modelSearch.placeholder = "Search model...";
        modelSearch.value = selectedModel;
      } catch (_) {
        modelSelect.innerHTML = `<option value="">Select model...</option>`;
        modelSearch.placeholder = "Search model...";
      } finally {
        setModelLoading(false);
      }
    };

    const applyModelSelection = (selectedModel) => {
      vehicle.model = selectedModel;
      modelSelect.value = selectedModel;
      modelSearch.value = selectedModel;
      hideModelResults();
      notifyChange();
    };

    const applyMakeSelection = async (selectedMake, { focusModel = false } = {}) => {
      vehicle.make = selectedMake;
      vehicle.model = "";

      makeSelect.value = selectedMake;
      makeSearch.value = selectedMake;
      modelSearch.value = "";
      hideMakeResults();
      hideModelResults();

      notifyChange();
      await populateModels(selectedMake);

      if (focusModel && selectedMake && !modelSelect.disabled) {
        modelSelect.focus();
      }

      notifyChange();
    };

    makes = await vehicleUiApiJSON("/api/makes");
    populateMakeOptions();
    await populateModels(vehicle.make, vehicle.model);
    notifyChange();

    yearSelect.addEventListener("change", () => {
      vehicle.year = yearSelect.value;
      notifyChange();
    });

    makeSearch.addEventListener("input", () => {
      renderMakeResults(makeSearch.value);
    });

    makeSearch.addEventListener("focus", () => {
      if (makeSearch.value.trim()) {
        renderMakeResults(makeSearch.value);
      }
    });

    makeSearch.addEventListener("blur", () => {
      setTimeout(hideMakeResults, 150);
    });

    makeResults.addEventListener("click", async (event) => {
      const resultButton = event.target.closest(".make-result-item");
      if (!resultButton) return;
      await applyMakeSelection(resultButton.dataset.make || "", { focusModel: true });
    });

    makeSelect.addEventListener("change", async () => {
      await applyMakeSelection(makeSelect.value, { focusModel: true });
    });

    modelSearch.addEventListener("input", () => {
      renderModelResults(modelSearch.value);
    });

    modelSearch.addEventListener("focus", () => {
      if (modelSearch.value.trim()) {
        renderModelResults(modelSearch.value);
      }
    });

    modelSearch.addEventListener("blur", () => {
      setTimeout(hideModelResults, 150);
    });

    modelResults.addEventListener("click", (event) => {
      const resultButton = event.target.closest(".model-result-item");
      if (!resultButton) return;
      applyModelSelection(resultButton.dataset.model || "");
    });

    modelSelect.addEventListener("change", () => {
      applyModelSelection(modelSelect.value);
    });

    clearButton?.addEventListener("click", async () => {
      vehicle.year = "";
      vehicle.make = "";
      vehicle.model = "";

      yearSelect.value = "";
      makeSelect.value = "";
      makeSearch.value = "";
      modelSearch.value = "";
      hideMakeResults();
      hideModelResults();
      await populateModels("");
      notifyChange();
    });

    return {
      getValue() {
        return { ...vehicle };
      },
    };
  }

  window.TorqueMechVehicleUI = Object.assign({}, window.TorqueMechVehicleUI, {
    initSharedVehicleSelector,
  });

  // Only run on Estimator page
  const estimateBtn = document.getElementById("quickEstimateBtn");
  if (!estimateBtn) return;


  function calculateTotals(estimate) {
    const labor = estimate.lines
      .filter(l => l.type === "labor")
      .reduce((sum, l) => {

        if (l.pricing_mode === "flat") {
          return sum + (Number(l.flat_rate) || 0);
        }

        return sum + (Number(l.hours) || 0) * (Number(l.rate) || 0);

      }, 0);

    const parts = estimate.lines
      .filter(l => l.type === "part")
      .reduce((sum, p) => sum + (Number(p.qty) || 0) * (Number(p.unit_price) || 0), 0);

    const taxRate = Number(estimate.tax_rate || 0); // e.g. 0.0825
    const suppliesRate = Number(estimate.supplies_rate || 0); // e.g. 0.05

    const supplies = parts * suppliesRate;
    const taxableParts = parts + supplies;  // common approach; can change later
    const tax = taxableParts * taxRate;

    const travel = Number(estimate.travel_fee || 0);

    const total = labor + parts + supplies + tax + travel;

    return { labor, parts, supplies, tax, travel, total };
  }

  // ---- DOM helpers ----
  const $ = (id) => document.getElementById(id);
  const laborBreakdownToggle = $("laborBreakdownToggle");
  const laborBreakdownContent = $("laborBreakdownContent");
  const laborBreakdownChevron = $("laborBreakdownChevron");
  const vehiclesContainer = $("vehiclesContainer");
  const addVehicleBtn = $("addVehicleBtn");

  function setLaborBreakdownExpanded(expanded) {
    if (!laborBreakdownToggle || !laborBreakdownContent) return;

    laborBreakdownToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    laborBreakdownContent.classList.toggle("hidden", !expanded);
  }

  laborBreakdownToggle?.addEventListener("click", () => {
    const expanded = laborBreakdownToggle.getAttribute("aria-expanded") === "true";
    setLaborBreakdownExpanded(!expanded);
  });

  // Vehicle
  let yearEl = $("year");
  let makeEl = $("make");
  let modelEl = $("model");

  // Service selection
  const categoryEl = $("category");
  const serviceEl = $("service");
  let serviceOptions = [];
  let serviceCategories = [];
  let allServiceOptions = [];
  let allServiceOptionsVehicleKey = "";
  let serviceSearch = null;
  let serviceResults = null;

  const QUICK_QUOTE_SHORTCUTS = {
    oil_change: {
      label: "Oil Change",
      category: "maintenance",
      serviceCode: "oil_and_filter_change",
    },
    front_brakes: {
      label: "Front Brake Job",
      category: "brakes",
      serviceCode: "front_brake_pads_and_rotors_replacement",
    },
    rear_brakes: {
      label: "Rear Brake Job",
      category: "brakes",
      serviceCode: "rear_brake_pads_and_rotors_replacement",
    },
    battery: {
      label: "Battery Replacement",
      category: "electrical",
      serviceCode: "battery_replacement",
    },
    alternator: {
      label: "Alternator Replacement",
      category: "electrical",
      serviceCode: "alternator_replacement",
    },
    starter: {
      label: "Starter Replacement",
      category: "electrical",
      serviceCode: "starter_replacement",
    },
    spark_plugs: {
      label: "Spark Plug Replacement",
      category: "engine",
      serviceCode: "spark_plug_replacement_4_cyl",
    },
    thermostat: {
      label: "Thermostat Replacement",
      category: "cooling",
      serviceCode: "thermostat_replacement",
    },
    suspension_inspection: {
      label: "Suspension Inspection",
      category: "suspension",
      serviceCode: "suspension_noise_diagnosis",
    },
  };

  const TIMING_SERVICE_CODES = {
    timingBelt: "timing_belt_replacement",
    timingChainKit: "timing_chain_guide_replacement",
    timingChainLegacy: "timing_chain_service",
  };

  const TIMING_SYSTEM_SUPPORT = {
    TOYOTA: {
      SEQUOIA: {
        type: "chain",
        show: [TIMING_SERVICE_CODES.timingChainKit],
      },
    },
  };

  if (serviceEl) {
    serviceSearch = serviceEl.parentElement?.querySelector(".service-search");
    if (!serviceSearch) {
      serviceSearch = document.createElement("input");
      serviceSearch.type = "text";
      serviceSearch.className = "service-search";
      serviceSearch.autocomplete = "off";
      serviceEl.insertAdjacentElement("beforebegin", serviceSearch);
    }
    serviceSearch.placeholder = "Choose a category first...";
    serviceSearch.disabled = true;

    serviceResults = serviceEl.parentElement?.querySelector(".service-results");
    if (!serviceResults) {
      serviceResults = document.createElement("div");
      serviceResults.className = "service-results";
      serviceSearch.insertAdjacentElement("afterend", serviceResults);
    }
    serviceResults.style.display = "none";
    serviceResults.style.marginTop = "6px";
    serviceResults.style.border = "1px solid rgba(255,255,255,.12)";
    serviceResults.style.borderRadius = "12px";
    serviceResults.style.overflowY = "auto";

    serviceEl.style.display = "none";
  }

  // Inputs
  const laborHoursEl = $("laborHours");
  const laborHoursRangeEl = $("laborHoursRange");
  const partsPriceEl = $("partsPrice");
  const laborRateEl = $("laborRate");
  const pricingModeEl = $("pricingMode");
  const flatRatePriceEl = $("flatRatePrice");
  const travelFeeEl = $("travelFee");
  const flatRateWrap = $("flatRateWrap");
  const hourlyPricingFields = Array.from(document.querySelectorAll(".hourly-pricing-field"));
  const notesEl = $("notes");

  // Buttons / UI
  const statusBox = $("statusBox");
  const clearBtn = $("clearBtn");
  const generateAllBtn = $("generateAllBtn");
  const addLineBtn = $("addLineBtn");
  const addServiceHint = $("addServiceHint");
  const getEstimateHint = $("getEstimateHint");
  const workflowStepText = $("workflowStepText");
  const quickEstimateBtn = document.getElementById("quickEstimateBtn");

  // Line items
  const lineItemsWrap = $("lineItemsWrap");
  const lineItemsList = $("lineItemsList");
  const pairedSuggestions = $("pairedSuggestions");
  const pairedSuggestionsList = $("pairedSuggestionsList");
  const estimateTotalBar = $("estimateTotalBar");
  const estimateTotalValue = $("estimateTotalValue");
  const sharedSnapshotVehicle = $("sharedSnapshotVehicle");
  const sharedSnapshotServices = $("sharedSnapshotServices");
  const sharedSnapshotTotal = $("sharedSnapshotTotal");
  const sharedDownloadPdfBtn = $("sharedDownloadPdfBtn");

  // Preview (optional)
  const estimatePreview = $("estimatePreview");
  const previewTotalText = $("previewTotalText");
  const previewSubText = $("previewSubText");

  const activeVehicleBanner = $("activeVehicleBanner");
  const activeVehicleText = $("activeVehicleText");

  // Confirm modal
  const confirmModal = $("confirmModal");
  const confirmBackdrop = $("confirmBackdrop");
  const confirmCloseBtn = $("confirmCloseBtn");
  const confirmAddBtn = $("confirmAddBtn");
  const copyQuoteBtn = $("copyQuoteBtn");
  const emailQuoteBtn = $("emailQuoteBtn");
  const quotePreviewEl = $("quotePreview");
  const confirmMsg = $("confirmMsg");
  const confirmServiceText = $("confirmServiceText");
  const confirmTotalText = $("confirmTotalText");
  const pdfShowGeneratedDateChk = $("pdfShowGeneratedDateChk");
  const pdfShowHourlyRateChk = $("pdfShowHourlyRateChk");
  const pdfShowLaborColumnChk = $("pdfShowLaborColumnChk");
  const pdfShowPartsColumnChk = $("pdfShowPartsColumnChk");
  const pdfShowLaborBreakdownChk = $("pdfShowLaborBreakdownChk");

  // Customer
  const customerNameEl = $("customerName");
  const customerPhoneEl = $("customerPhone");

  // VIN
  const vinEl = $("vin");
  const vinLookupBtn = $("vinLookupBtn");
  const vinToggle = $("vinToggle");
  const vinPanel = $("vinPanel");

  const vinDecodedMeta = $("vinDecodedMeta");

  // PWA install (optional)
  const installBtn = $("installBtn");

  // Signature
  const sigSection = $("sigSection");
  const sigCanvas = $("sigCanvas");
  const sigClearBtn = $("sigClearBtn");
  const customerAgreesChk = $("customerAgreesChk");

  let sigCtx = sigCanvas ? sigCanvas.getContext("2d") : null;

  // ===== NEW MULTI-VEHICLE STATE (Phase 1 Safe) =====
  let estimateState = {
    customer: {
      name: "",
      phone: "",
      email: ""
    },
    activeVehicleId: "veh_1",
    vehicles: [
      {
        id: "veh_1",
        year: "",
        make: "",
        model: "",
        services: []
      }
    ]
  };

  window.estimateState = estimateState;

  // ---- State ----
  let lineItems = [];

  const COMMONLY_ADDED_TOGETHER = [
    {
      match: ["brake pad", "brake pads"],
      suggestions: [
        { label: "Brake Rotors", query: "brake rotor" },
        { label: "Brake Fluid Flush", query: "brake fluid" },
      ],
    },
    {
      match: ["spark plug", "spark plugs"],
      suggestions: [
        { label: "Ignition Coils", query: "ignition coil" },
      ],
    },
    {
      match: ["thermostat"],
      suggestions: [
        { label: "Coolant Flush", query: "coolant flush" },
      ],
    },
    {
      match: ["battery"],
      suggestions: [
        { label: "Battery Terminal Service", query: "battery terminal" },
      ],
    },
    {
      match: ["serpentine belt", "drive belt"],
      suggestions: [
        { label: "Belt Tensioner", query: "belt tensioner" },
        { label: "Idler Pulley", query: "idler pulley" },
      ],
    },
  ];

  function getCustomerOutputOptions() {
    return {
      showGeneratedDate: pdfShowGeneratedDateChk ? !!pdfShowGeneratedDateChk.checked : true,
      showHourlyRate: pdfShowHourlyRateChk ? !!pdfShowHourlyRateChk.checked : false,
      showLaborColumn: pdfShowLaborColumnChk ? !!pdfShowLaborColumnChk.checked : false,
      showPartsColumn: pdfShowPartsColumnChk ? !!pdfShowPartsColumnChk.checked : false,
      showDetailedLaborBreakdown: pdfShowLaborBreakdownChk ? !!pdfShowLaborBreakdownChk.checked : false,
    };
  }

  // Sync lineItems to Vehicle 1 (temporary bridge)
  function syncLineItemsToVehicle() {
    for (const vehicle of estimateState.vehicles) {
      vehicle.services = lineItems
        .filter(it => it.vehicleId === vehicle.id)
        .map((it, index) => ({
          id: it.id || `svc_${vehicle.id}_${index}`,
          vehicleId: vehicle.id,
          vehicleLabel: it.vehicleLabel || getVehicleLabel(vehicle),
          serviceCode: it.serviceCode,
          title: it.serviceText,
          laborHours: it.laborHours,
          laborRate: it.laborRate,
          partsTotal: it.partsPrice,
          lineTotal: it.estimate,
          notes: it.notes || ""
        }));
    }

    window.estimateState = estimateState;
  }

  function syncEstimateMeta() {
    if (!estimateState.vehicles.length) return;

    estimateState.customer.name = customerNameEl?.value || "";
    estimateState.customer.phone = customerPhoneEl?.value || "";

    window.estimateState = estimateState;
  }

  function getActiveVehicle() {
    return estimateState.vehicles.find(v => v.id === estimateState.activeVehicleId) || estimateState.vehicles[0] || null;
  }

  function getCurrentVehicleSnapshot() {
    const vehicle = getActiveVehicle() || estimateState.vehicles[0] || null;
    return {
      year: vehicle?.year || "",
      make: vehicle?.make || "",
      model: vehicle?.model || "",
    };
  }

  function getVehicleLabel(vehicle, idxOverride = null) {
    if (!vehicle) return "No vehicle selected";

    const idx = idxOverride ?? estimateState.vehicles.findIndex(v => v.id === vehicle.id);
    const title = `Vehicle ${idx + 1}`;
    const details = [vehicle.year, vehicle.make, vehicle.model].filter(Boolean).join(" ");

    return details ? `${title} — ${details}` : title;
  }

  function setActiveVehicle(vehicleId) {
    const exists = estimateState.vehicles.some(v => v.id === vehicleId);
    if (!exists) return;

    estimateState.activeVehicleId = vehicleId;
    window.estimateState = estimateState;
    void renderVehicles();
    renderActiveVehicleBanner();
    refreshQuotePreview();
  }

  function renderActiveVehicleBanner() {
    if (!activeVehicleBanner) return;
    activeVehicleBanner.style.display = "none";
  }
  // { serviceCode, serviceText, pricingMode, flatRatePrice, travelFee, laborHours, partsPrice, laborRate, notes, estimate }
  let lastEstimate = null; // { req, res }
  let serviceMeta = null;
  let signatureDataUrl = null;
  let editingLineItem = null; // { serviceCode, serviceText }
  let activeEditingLineId = null;

  function createLineItemId() {
    if (window.crypto?.randomUUID) {
      return `line_${window.crypto.randomUUID()}`;
    }
    return `line_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  }

  function getLineItemById(lineItemId) {
    return lineItems.find((it) => it.id === lineItemId) || null;
  }

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

  function createShareId() {
    if (window.crypto?.randomUUID) {
      return window.crypto.randomUUID();
    }

    const randomByte = () => {
      const bytes = new Uint8Array(1);
      if (window.crypto?.getRandomValues) {
        window.crypto.getRandomValues(bytes);
        return bytes[0];
      }
      return Math.floor(Math.random() * 256);
    };

    return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
      (Number(c) ^ ((randomByte() & 15) >> (Number(c) / 4))).toString(16)
    );
  }

  function buildShareLink(d) {
    if (!d?.shareId) return "";
    return `${window.location.origin}/estimate/share/${encodeURIComponent(d.shareId)}`;
  }

  function showEstimateSavedBlock(d) {
    lastSavedEstimateLink = buildShareLink(d);
    if (!estimateSavedBlock || !lastSavedEstimateLink) return;

    estimateSavedBlock.hidden = false;
    if (estimateSavedLinkText) {
      estimateSavedLinkText.textContent = lastSavedEstimateLink;
    }
  }

  function sharedEstimateIdFromPath() {
    const match = String(window.location.pathname || "").match(/^\/estimate\/share\/([0-9a-f-]{36})$/i);
    return match ? match[1] : "";
  }

  async function loadSharedEstimateFromPath() {
    const shareId = sharedEstimateIdFromPath();
    if (!shareId) return;

    const draft = getDrafts().find((d) => String(d.shareId || "").toLowerCase() === shareId.toLowerCase());
    if (!draft) {
      if (draftsMsg) draftsMsg.textContent = "This share link is ready, but the saved estimate is not stored on this device.";
      return;
    }

    await applyDraft(draft);
    showEstimateSavedBlock(draft);
    if (draftsMsg) draftsMsg.textContent = `Opened saved estimate from this device: ${draft.title}`;
  }

  function refreshDraftsUI() {
    if (!draftsSelect) return;
    if (document.getElementById("draftsCard")?.dataset.savedEstimatesDisabled === "true") {
      draftsSelect.innerHTML = `<option value="">Unavailable</option>`;
      if (draftsMsg) draftsMsg.textContent = "";
      return;
    }

    const drafts = getDrafts();
    draftsSelect.innerHTML = `<option value="">Select a device-saved estimate</option>` +
      drafts.map(d => `<option value="${d.id}">${draftLabel(d)}</option>`).join("");

    if (draftsMsg) {
      draftsMsg.textContent = drafts.length
        ? `Saved on this device: ${drafts.length}. Load one to continue an estimate, compare repair paths, or prepare customer approval.`
        : "No saved estimates on this device yet. Build an estimate, then save it here to return later.";
    }
  }

  function buildDraftTitle() {
    const currentVehicle = getCurrentVehicleSnapshot();
    const vehicle = [currentVehicle.year, currentVehicle.make, currentVehicle.model].filter(Boolean).join(" ");
    const servicesCount = lineItems.length;
    return (vehicle || "Estimate") + (servicesCount ? ` (${servicesCount} service${servicesCount > 1 ? "s" : ""})` : "");
  }

  function serializeDraft() {
    const currentVehicle = getCurrentVehicleSnapshot();
    return {
      id: String(Date.now()),
      shareId: createShareId(),
      savedAt: Date.now(),
      title: buildDraftTitle(),

      vehicle: {
        year: currentVehicle.year,
        make: currentVehicle.make,
        model: currentVehicle.model,
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

  function normalizeDraftLineItem(it, fallbackVehicle) {
    const pricingMode = (it?.pricingMode || "hourly").trim() === "flat" ? "flat" : "hourly";
    const vehicleId = it?.vehicleId || fallbackVehicle.id;
    const vehicleYear = it?.vehicleYear || fallbackVehicle.year || "";
    const vehicleMake = it?.vehicleMake || fallbackVehicle.make || "";
    const vehicleModel = it?.vehicleModel || fallbackVehicle.model || "";
    const vehicleLabel = it?.vehicleLabel || getVehicleLabel({ id: vehicleId, year: vehicleYear, make: vehicleMake, model: vehicleModel }, 0);

    return {
      ...it,
      id: it?.id || createLineItemId(),
      vehicleId,
      vehicleLabel,
      vehicleYear,
      vehicleMake,
      vehicleModel,
      serviceCode: String(it?.serviceCode || "").trim(),
      serviceText: String(it?.serviceText || it?.serviceCode || "Service").trim(),
      pricingMode,
      flatRatePrice: Number(it?.flatRatePrice || 0),
      travelFee: Number(it?.travelFee || 0),
      laborHours: Number(it?.laborHours || 0),
      partsPrice: Number(it?.partsPrice || 0),
      laborRate: Number(it?.laborRate || 0),
      notes: (it?.notes || "").trim() || null,
      estimate: it?.estimate != null ? Number(it.estimate) : null,
      laborBreakdown: it?.laborBreakdown || null,
      breakdownOpen: false,
    };
  }

    async function applyDraft(d) {
      if (!d) return;

      try {
        closeConfirm();
      } catch (_) {}

      // Reset to a safe default state first
      estimateState = {
        customer: {
          name: d.customer?.name || "",
          phone: d.customer?.phone || "",
          email: ""
        },
        activeVehicleId: "veh_1",
        vehicles: [
          {
            id: "veh_1",
            year: d.vehicle?.year || "",
            make: d.vehicle?.make || "",
            model: d.vehicle?.model || "",
            services: []
          }
        ]
      };

      window.estimateState = estimateState;

      // Legacy draft support:
      // if old line items do not have vehicleId / vehicleLabel, assign them to Vehicle 1
      lineItems = (Array.isArray(d.lineItems) ? d.lineItems : []).map((it) =>
        normalizeDraftLineItem(it, estimateState.vehicles[0])
      );
      estimateState.activeVehicleId = "veh_1";

      // Sync top-level customer fields
      if (customerAgreesChk) customerAgreesChk.checked = !!d.customer?.agrees;
      if (customerNameEl) customerNameEl.value = d.customer?.name || "";
      if (customerPhoneEl) customerPhoneEl.value = d.customer?.phone || "";
      if (notesEl) notesEl.value = d.customer?.notes || "";
      const wantYes = document.querySelector('input[name="wantSig"][value="yes"]');
      if (wantYes) wantYes.checked = true;

      // Re-render vehicle cards from restored estimateState
      await renderVehicles();
      renderActiveVehicleBanner();

      // Re-render service cards
      renderLineItems();
      void refreshPairedSuggestions();

      // Reset signature (Beta-safe)
      signatureDataUrl = null;
      setSigVisible(false);
      clearSignatureCanvas();

      // Reset editor state
      activeEditingLineId = null;
      editingLineItem = null;
      laborHoursTouched = false;
      readyForNextService = true;
      togglePricingModeUI();
      refreshQuotePreview();
      updateEstimateButtonState();

      if (draftsMsg) draftsMsg.textContent = `Loaded from this device: ${d.title}`;
    }

  function saveCurrentDraft() {
    const d = serializeDraft();
    const drafts = getDrafts();

    drafts.unshift(d);
    if (drafts.length > MAX_DRAFTS) drafts.length = MAX_DRAFTS;

    setDrafts(drafts);
    refreshDraftsUI();
    if (draftsSelect) draftsSelect.value = d.id;
    showEstimateSavedBlock(d);

    if (draftsMsg) draftsMsg.textContent = `Saved on this device for later approval, parts pricing, or repair comparison: ${d.title}`;
  }

  async function loadSelectedDraft() {
    const id = draftsSelect?.value;
    if (!id) {
      if (draftsMsg) draftsMsg.textContent = "Select a device-saved estimate to continue.";
      return;
    }

    const drafts = getDrafts();
    const d = drafts.find(x => x.id === id);
    if (!d) {
      if (draftsMsg) draftsMsg.textContent = "Saved estimate not found on this device.";
      return;
    }

    await applyDraft(d);
  }

  function deleteSelectedDraft() {
    const id = draftsSelect?.value;
    if (!id) {
      if (draftsMsg) draftsMsg.textContent = "Select a device-saved estimate first.";
      return;
    }

    const drafts = getDrafts().filter(x => x.id !== id);
    setDrafts(drafts);
    refreshDraftsUI();
    if (draftsSelect) draftsSelect.value = "";

    if (draftsMsg) draftsMsg.textContent = "Saved estimate deleted from this device.";
  }

  let laborHoursTouched = false;
  let readyForNextService = true; // ✅ the lock/unlock flag

  // Track if user manually edited labor hours (so we don't overwrite it)
  laborHoursEl?.addEventListener("input", () => {
    laborHoursTouched = true;
    syncLivePricingFromInputs();
  });
  laborRateEl?.addEventListener("input", syncLivePricingFromInputs);
  partsPriceEl?.addEventListener("input", syncLivePricingFromInputs);
  flatRatePriceEl?.addEventListener("input", syncLivePricingFromInputs);
  travelFeeEl?.addEventListener("input", syncLivePricingFromInputs);

  function installPricingNumberFocusBehavior(inputEl, fallbackValue = "0") {
    if (!inputEl) return;

    let replaceOnNextEntry = false;
    let focusedValue = "";
    const isZeroValue = (value) => /^0(?:\.0+)?$/.test(String(value || "").trim());
    const sanitizeDecimalValue = (value) => {
      let sanitized = "";
      let hasDecimal = false;

      for (const char of String(value || "")) {
        if (char >= "0" && char <= "9") {
          sanitized += char;
          continue;
        }
        if (char === "." && !hasDecimal) {
          sanitized += char;
          hasDecimal = true;
        }
      }

      return sanitized;
    };
    const selectCurrentValue = () => {
      if (!inputEl.value) return;
      try {
        inputEl.select();
      } catch (_) {}
      try {
        inputEl.setSelectionRange(0, String(inputEl.value || "").length);
      } catch (_) {}
    };

    const prepareForReplace = () => {
      const value = String(inputEl.value || "").trim();
      focusedValue = value;
      replaceOnNextEntry = false;

      if (isZeroValue(value)) {
        inputEl.value = "";
        focusedValue = "";
        return;
      }

      replaceOnNextEntry = true;
      selectCurrentValue();
      window.requestAnimationFrame?.(selectCurrentValue);
      window.setTimeout(selectCurrentValue, 0);
    };

    inputEl.addEventListener("focus", prepareForReplace);
    inputEl.addEventListener("click", prepareForReplace);
    inputEl.addEventListener("touchend", () => {
      window.setTimeout(prepareForReplace, 0);
    });

    inputEl.addEventListener("beforeinput", (event) => {
      if (!replaceOnNextEntry) return;
      if (!String(event.inputType || "").startsWith("insert")) return;

      const insertedValue = sanitizeDecimalValue(
        event.data || event.clipboardData?.getData("text") || ""
      );
      if (insertedValue) {
        event.preventDefault();
        inputEl.value = insertedValue;
        if (inputEl === laborHoursEl) {
          laborHoursTouched = true;
        }
        syncLivePricingFromInputs();
      } else if (String(inputEl.value || "") === focusedValue) {
        inputEl.value = "";
      }
      replaceOnNextEntry = false;
    });

    inputEl.addEventListener("blur", () => {
      replaceOnNextEntry = false;
      const sanitizedValue = sanitizeDecimalValue(inputEl.value);
      inputEl.value = sanitizedValue;
      if (sanitizedValue !== "" && sanitizedValue !== ".") return;
      inputEl.value = fallbackValue;
      if (inputEl === laborHoursEl) {
        laborHoursTouched = false;
      }
      syncLivePricingFromInputs();
    });

    inputEl.addEventListener("input", () => {
      if (replaceOnNextEntry && focusedValue) {
        const currentValue = String(inputEl.value || "");
        const replacementValue = sanitizeDecimalValue(currentValue.replace(focusedValue, ""));
        if (replacementValue && replacementValue !== currentValue) {
          inputEl.value = replacementValue;
        }
      }
      const sanitizedValue = sanitizeDecimalValue(inputEl.value);
      if (sanitizedValue !== inputEl.value) {
        inputEl.value = sanitizedValue;
      }
      replaceOnNextEntry = false;
    }, true);
  }

  installPricingNumberFocusBehavior(laborHoursEl, "0");
  installPricingNumberFocusBehavior(partsPriceEl, "0");
  installPricingNumberFocusBehavior(laborRateEl, "90");
  installPricingNumberFocusBehavior(flatRatePriceEl, "0");
  installPricingNumberFocusBehavior(travelFeeEl, "0");

  // ---- Utils ----
  function setStatus(kind, msg) {
    if (!statusBox) return;
    statusBox.dataset.kind = kind || "info";
    statusBox.textContent = msg || "";
  }

  function setConfirmMessage(kind, msg) {
    if (!confirmMsg) return;
    confirmMsg.dataset.kind = kind || "info";
    confirmMsg.textContent = msg || "";
  }

  function clearConfirmMessage() {
    setConfirmMessage("info", "");
  }

  function money(n) {
    const x = Number(n || 0);
    return `$${Math.round(x).toLocaleString()}`;
  }

  function pricingInputNumber(inputEl, fallbackValue = 0) {
    const parsed = parseFloat(String(inputEl?.value ?? "").trim());
    return Number.isFinite(parsed) ? parsed : fallbackValue;
  }

  function getPricingMode() {
  return (pricingModeEl?.value || "hourly").trim();
}

function togglePricingModeUI() {
  const isFlat = getPricingMode() === "flat";

  flatRateWrap?.classList.toggle("hidden", !isFlat);
  hourlyPricingFields.forEach((el) => {
    el.classList.toggle("hidden", isFlat);
  });

  if (statusBox) {
    statusBox.textContent = isFlat
      ? "Flat Rate mode: job price, parts, and travel update the estimate automatically."
      : "Hourly mode: labor, parts, and travel update the estimate automatically.";
    statusBox.dataset.kind = "info";
  }

  if (laborHoursRangeEl && isFlat) {
    laborHoursRangeEl.textContent = "";
  } else {
    updateLaborRangeUI();
  }
}

const confidenceEl = document.getElementById("laborConfidence");

  function quoteTotal() {
    return lineItems.reduce((sum, it) => sum + Number(it.estimate || 0), 0);
  }

  function formatRunningTotal(n) {
    return Number(n || 0).toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function renderEstimateTotalBar() {
    if (estimateTotalValue) {
      estimateTotalValue.textContent = formatRunningTotal(quoteTotal());
    }
    if (estimateTotalBar) {
      estimateTotalBar.dataset.empty = lineItems.length ? "false" : "true";
    }
    renderEstimatePreview();
    renderSharedEstimateSnapshot();
  }

  function renderEstimatePreview() {
    if (!estimatePreview || !previewTotalText || !previewSubText) return;

    if (!lineItems.length) {
      estimatePreview.classList.add("hidden");
      previewTotalText.textContent = "—";
      previewSubText.textContent = "";
      return;
    }

    const total = quoteTotal();
    estimatePreview.classList.remove("hidden");
    previewTotalText.textContent = formatRunningTotal(total);
    previewSubText.textContent = `${lineItems.length} service${lineItems.length === 1 ? "" : "s"} • updates live as pricing changes`;
  }

  function renderSharedEstimateSnapshot() {
    if (!sharedSnapshotVehicle && !sharedSnapshotServices && !sharedSnapshotTotal) return;

    const currentVehicle = getCurrentVehicleSnapshot();
    const vehicle = [currentVehicle.year, currentVehicle.make, currentVehicle.model].filter(Boolean).join(" ");

    if (sharedSnapshotVehicle) {
      sharedSnapshotVehicle.textContent = vehicle || "Not selected";
    }
    if (sharedSnapshotServices) {
      sharedSnapshotServices.textContent = lineItems.length
        ? `${lineItems.length} service${lineItems.length === 1 ? "" : "s"}`
        : "No services added";
    }
    if (sharedSnapshotTotal) {
      sharedSnapshotTotal.textContent = formatRunningTotal(quoteTotal());
    }
  }

  function buildQuoteMessage() {
    const customerName = (customerNameEl?.value || "").trim();
    const currentVehicle = getCurrentVehicleSnapshot();
    const vehicle = [currentVehicle.year, currentVehicle.make, currentVehicle.model].filter(Boolean).join(" ");
    const total = quoteTotal();

    const lines = [];

    if (customerName) {
      lines.push(`Hi ${customerName},`);
      lines.push("");
    } else {
      lines.push("Hi,");
      lines.push("");
    }

    lines.push("Here is the repair quote prepared for your vehicle:");
    lines.push("");

    if (vehicle) {
      lines.push(`Vehicle: ${vehicle}`);
      lines.push("");
    }

    lineItems.forEach((it) => {
      if (it.pricingMode === "flat") {
        lines.push(`• ${it.serviceText}: ${money(it.estimate)} (flat-rate${Number(it.travelFee || 0) > 0 ? `, includes ${money(it.travelFee)} travel` : ""})`);
      } else {
        lines.push(`• ${it.serviceText}: ${money(it.estimate)}${Number(it.travelFee || 0) > 0 ? ` (includes ${money(it.travelFee)} travel)` : ""}`);
      }
    });

    lines.push("");
    lines.push(`Quote Total: ${money(total)}`);

    const notes = (notesEl?.value || "").trim();
    if (notes) {
      lines.push("");
      lines.push(`Notes: ${notes}`);
    }

    lines.push("");
    lines.push("Final pricing may vary after inspection, parts confirmation, taxes, or added repair needs.");

    return lines.join("\n");
  }

  function buildEstimateEmailSubject() {
    const currentVehicle = getCurrentVehicleSnapshot();
    const vehicle = [currentVehicle.year, currentVehicle.make, currentVehicle.model].filter(Boolean).join(" ");
    const serviceCount = lineItems.length;
    const serviceText = serviceCount === 1
      ? lineItems[0]?.serviceText
      : `${serviceCount} Service Estimate`;

    return [vehicle, serviceText || "Repair Estimate"].filter(Boolean).join(" - ");
  }

  function buildEstimateEmailBody() {
    const lines = [buildQuoteMessage()];
    const path = window.location?.pathname || "";

    if (path.startsWith("/estimate/share/")) {
      lines.push("");
      lines.push(`Estimate link: ${window.location.href}`);
    }

    return lines.join("\n");
  }

  function emailEstimate() {
    if (!lineItems.length) {
      setStatus("error", "Add at least one service before emailing an estimate.");
      setConfirmMessage("error", "Add at least one service before emailing an estimate.");
      return;
    }

    refreshQuotePreview();

    const subject = encodeURIComponent(buildEstimateEmailSubject());
    const body = encodeURIComponent(buildEstimateEmailBody());
    window.location.href = `mailto:?subject=${subject}&body=${body}`;

    setConfirmMessage("info", "Opening your email app with this estimate.");
    setStatus("ok", "Opening your email app with this estimate.");
  }

  function calcLineItemEstimate(it) {
    const pricingMode = (it.pricingMode || "hourly").trim();
    const parts = Number(it.partsPrice || 0);
    const travel = Number(it.travelFee || 0);

    let base = 0;

    if (pricingMode === "flat") {
      base = Number(it.flatRatePrice || 0) + parts;
    } else {
      const laborHours = Number(it.laborHours || 0);
      const laborRate = Number(it.laborRate || 0);
      base = (laborHours * laborRate) + parts;
    }

    return Math.round(base + travel);
  }

  function syncLivePricingFromInputs() {
    // Pricing Controls are draft inputs only. Never live-write them into saved quote cards.
    // Saved cards are updated only when a new line item snapshot is added or an explicit card edit mode is introduced.
    refreshQuotePreview();
  }

  function buildPricingSnapshotFromControls() {
    return {
      pricingMode: getPricingMode(),
      flatRatePrice: pricingInputNumber(flatRatePriceEl),
      travelFee: pricingInputNumber(travelFeeEl),
      laborHours: pricingInputNumber(laborHoursEl),
      partsPrice: pricingInputNumber(partsPriceEl),
      laborRate: pricingInputNumber(laborRateEl),
    };
  }

  function loadPricingSnapshotIntoControls(it) {
    if (!it) return;
    if (pricingModeEl) pricingModeEl.value = it.pricingMode === "flat" ? "flat" : "hourly";
    if (flatRatePriceEl) flatRatePriceEl.value = String(Number(it.flatRatePrice || 0));
    if (travelFeeEl) travelFeeEl.value = String(Number(it.travelFee || 0));
    if (laborHoursEl) laborHoursEl.value = String(Number(it.laborHours || 0));
    if (partsPriceEl) partsPriceEl.value = String(Number(it.partsPrice || 0));
    if (laborRateEl) laborRateEl.value = String(Number(it.laborRate || 0));
    laborHoursTouched = true;
    togglePricingModeUI();
  }

  function buildLineItemEstimateRequest(it) {
    return {
      year: Number(it.vehicleYear || 0),
      make: String(it.vehicleMake || "").trim(),
      model: String(it.vehicleModel || "").trim(),
      serviceCode: it.serviceCode,
      pricingMode: it.pricingMode,
      flatRatePrice: Number(it.flatRatePrice || 0),
      travelFee: Number(it.travelFee || 0),
      laborHours: Number(it.laborHours || 0),
      partsPrice: Number(it.partsPrice || 0),
      laborRate: Number(it.laborRate || 0),
      notes: it.notes,
    };
  }

  async function recalculateLineItemFromSnapshot(it, action = "line_item_recalculate") {
    if (!it) return;

    if (it.pricingMode === "flat") {
      it.estimate = calcLineItemEstimate(it);
      return;
    }

    const req = buildLineItemEstimateRequest(it);
    const res = await apiJSON("/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });

    it.laborBreakdown = res.labor_breakdown || null;
    it.breakdownOpen = false;
    it.estimate = calcLineItemEstimate(it);
    lastEstimate = { req, res };

    trackClarity("estimate_generated", {
      source: "estimator",
      action,
      service_code: it.serviceCode,
      service_name: it.serviceText,
      estimate_total: Number(it.estimate || 0)
    });
  }

  function refreshQuotePreview() {
    if (!quotePreviewEl) return;
    quotePreviewEl.value = buildQuoteMessage();
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

          setStatus("info", "Diagnostic category selected from vehicle codes.");
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
            <div style="font-weight:800; font-size:16px;">Vehicle Codes Added</div>
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
    if (descEl) descEl.textContent = "Added to quote notes. You can edit or remove them.";

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
            setStatus("ok", `Vehicle code ${code} attached. Diagnostic service selected.`);
            return;
          }
        }

        // Fallback if we can’t auto-map
        updateEstimateButtonState();
        setStatus("info", `Vehicle code ${code} added to notes. Select a diagnostic service to quote.`);
      } catch (e) {
        setStatus("error", `Vehicle code handoff failed: ${e.message}`);
      }
    });

    pricingModeEl?.addEventListener("change", () => {
      togglePricingModeUI();
      syncLivePricingFromInputs();
      refreshQuotePreview?.();
    });

    dismissBtn?.addEventListener("click", () => {
      // Remove query param so refresh doesn’t re-run it
      const url = new URL(window.location.href);
      url.searchParams.delete("obd");
      window.history.replaceState({}, "", url.toString());

      // Hide card
      const c = document.getElementById("obdBridgeCard");
      if (c) c.style.display = "none";
      setStatus("info", "Vehicle code note dismissed.");
    });

  // Saved drafts (local)
  const saveDraftBtn = $("saveDraftBtn");
  const draftsSelect = $("draftsSelect");
  const loadDraftBtn = $("loadDraftBtn");
  const deleteDraftBtn = $("deleteDraftBtn");
  const draftsMsg = $("draftsMsg");
  const estimateSavedBlock = $("estimateSavedBlock");
  const copySavedEstimateLinkBtn = $("copySavedEstimateLinkBtn");
  const openSavedEstimateBtn = $("openSavedEstimateBtn");
  const downloadSavedEstimatePdfBtn = $("downloadSavedEstimatePdfBtn");
  const estimateSavedLinkText = $("estimateSavedLinkText");
  let lastSavedEstimateLink = "";

  // ---- Years / Makes / Models ----
  function populateYears() {
    if (!yearEl) return;

    const currentYear = new Date().getFullYear();
    const startYear = currentYear;
    const endYear = 1982;

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

    const selectedYear = Number(yearEl?.value || 0);

    modelEl.innerHTML = `<option value="">Loading models…</option>`;
    modelEl.disabled = true;

    if (!make) {
      modelEl.innerHTML = `<option value="">Select model…</option>`;
      modelEl.disabled = false;
      return;
    }

    try {
      let models;
      if (selectedYear) {
        models = await apiJSON(`/api/models/${encodeURIComponent(make)}?year=${selectedYear}`);
      } else {
        models = await apiJSON(`/api/models/${encodeURIComponent(make)}`);
      }

      modelEl.innerHTML = `<option value="">Select model…</option>`;
      for (const m of models) {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        modelEl.appendChild(opt);
      }
    } finally {
      modelEl.disabled = false;
    }
  }

  // ---- Categories / services ----
  function hideServiceResults() {
    if (!serviceResults) return;
    serviceResults.style.display = "none";
    serviceResults.innerHTML = "";
  }

  function syncServiceSearchFromSelect() {
    if (!serviceSearch || !serviceEl) return;
    const selectedText = serviceEl.options[serviceEl.selectedIndex]?.textContent?.trim() || "";
    serviceSearch.value = serviceEl.value ? selectedText : "";
  }

  const SERVICE_SEARCH_ALIASES = {
    engine: [
      "oil change",
      "engine air filter",
      "spark plug",
      "ignition coil",
      "coolant flush",
      "thermostat",
      "timing belt",
      "timing chain",
      "valve cover gasket",
      "engine diagnostic",
      "diagnostic",
    ],
    brake: ["brake pad", "brake rotor", "brake caliper", "brake fluid", "brake fluid flush"],
    battery: ["battery", "alternator", "starter", "charging", "no start"],
    overheat: ["thermostat", "radiator", "water pump", "coolant flush", "cooling", "coolant"],
    overheating: ["thermostat", "radiator", "water pump", "coolant flush", "cooling", "coolant"],
    misfire: ["spark plug", "ignition coil", "fuel injector", "injector", "engine diagnostic", "diagnostic"],
  };

  const SERVICE_SEARCH_CLUSTERS = {
    overheating: {
      codes: [
        "thermostat_replacement",
        "radiator_replacement",
        "water_pump_replacement",
        "coolant_flush",
        "cooling_system_pressure_test",
        "coolant_temperature_sensor_replacement",
        "cooling_fan_assembly_replacement",
        "cooling_fan_motor_replacement",
        "overheating_diagnosis",
      ],
      terms: ["overheat", "overheating", "cooling fan", "coolant leak", "pressure test"],
    },
    overheat: {
      codes: [
        "thermostat_replacement",
        "radiator_replacement",
        "water_pump_replacement",
        "coolant_flush",
        "cooling_system_pressure_test",
        "coolant_temperature_sensor_replacement",
        "cooling_fan_assembly_replacement",
        "cooling_fan_motor_replacement",
        "overheating_diagnosis",
      ],
      terms: ["overheat", "overheating", "cooling fan", "coolant leak", "pressure test"],
    },
    misfire: {
      codes: [
        "misfire_diagnosis",
        "spark_plug_replacement_4_cyl",
        "spark_plug_replacement_v6_v8",
        "ignition_coil_replacement_each",
        "compression_test",
        "cylinder_leak_down_test",
        "fuel_injector_replacement_each",
        "vacuum_leak_diagnosis_smoke_test",
      ],
      terms: ["misfire", "p0300", "p0303", "spark plug", "ignition coil", "injector", "compression"],
    },
    roughidle: {
      codes: [
        "misfire_diagnosis",
        "spark_plug_replacement_4_cyl",
        "spark_plug_replacement_v6_v8",
        "ignition_coil_replacement_each",
        "vacuum_leak_diagnosis_smoke_test",
        "throttle_body_cleaning",
        "throttle_body_replacement",
        "throttle_body_service",
        "mass_air_flow_sensor_replacement",
        "map_sensor_replacement",
        "pcv_valve_replacement",
        "fuel_trim_diagnosis",
      ],
      terms: ["rough idle", "idle", "stall", "vacuum leak", "throttle body", "maf"],
    },
    nostart: {
      codes: [
        "no_start_diagnosis",
        "starter_diagnosis",
        "alternator_diagnosis",
        "battery_replacement",
        "battery_cable_replacement",
        "no_crank_diagnosis",
        "crank_no_start_diagnosis",
        "fuel_pump_replacement_in_tank",
        "fuel_pump_replacement_external",
        "fuel_pressure_test",
        "fuel_system_diagnostic",
      ],
      terms: ["no start", "won't start", "starter", "alternator", "battery", "fuel pump"],
    },
    nocrank: {
      codes: [
        "no_crank_diagnosis",
        "starter_diagnosis",
        "starter_replacement",
        "starter_relay_replacement",
        "battery_replacement",
        "battery_cable_replacement",
        "ground_strap_repair",
        "electrical_diagnostic",
      ],
      terms: ["no crank", "won't crank", "clicking", "starter", "battery cable", "ground"],
    },
    cranknostart: {
      codes: [
        "crank_no_start_diagnosis",
        "no_start_diagnosis",
        "no_start_fuel_diagnosis",
        "fuel_pressure_test",
        "fuel_pump_replacement_in_tank",
        "fuel_pump_replacement_external",
        "fuel_pump_control_module_replacement",
        "crankshaft_position_sensor_replacement",
        "camshaft_position_sensor_replacement",
        "compression_test",
      ],
      terms: ["crank no start", "cranks but won't start", "fuel pressure", "spark", "compression"],
    },
    startsthendies: {
      codes: [
        "crank_no_start_diagnosis",
        "fuel_pump_replacement_in_tank",
        "fuel_pump_replacement_external",
        "fuel_pump_control_module_replacement",
        "fuel_pressure_test",
        "mass_air_flow_sensor_replacement",
        "throttle_body_cleaning",
        "throttle_body_service",
        "fuel_trim_diagnosis",
      ],
      terms: ["starts then dies", "stall", "fuel pressure", "maf", "throttle body"],
    },
    hesitation: {
      codes: [
        "driveability_diagnosis",
        "fuel_trim_diagnosis",
        "mass_air_flow_sensor_replacement",
        "spark_plug_replacement_4_cyl",
        "spark_plug_replacement_v6_v8",
        "ignition_coil_replacement_each",
        "fuel_injector_replacement_each",
        "fuel_pressure_test",
        "throttle_body_service",
      ],
      terms: ["hesitation", "stumble", "under load", "driveability", "fuel trim"],
    },
    lossofpower: {
      codes: [
        "driveability_diagnosis",
        "fuel_pressure_test",
        "fuel_pump_replacement_in_tank",
        "fuel_pump_replacement_external",
        "mass_air_flow_sensor_replacement",
        "catalytic_converter_replacement",
        "catalyst_efficiency_diagnosis",
        "fuel_trim_diagnosis",
      ],
      terms: ["loss of power", "low power", "under load", "catalyst", "fuel pressure"],
    },
    poorfueleconomy: {
      codes: [
        "driveability_diagnosis",
        "fuel_trim_diagnosis",
        "mass_air_flow_sensor_replacement",
        "oxygen_sensor_replacement_upstream",
        "oxygen_sensor_replacement_downstream",
        "air_fuel_ratio_sensor_replacement",
        "spark_plug_replacement_4_cyl",
        "spark_plug_replacement_v6_v8",
        "fuel_injector_cleaning_on_car",
      ],
      terms: ["poor fuel economy", "mpg", "fuel trim", "oxygen sensor", "air fuel"],
    },
    shaking: {
      codes: [
        "misfire_diagnosis",
        "engine_mount_replacement",
        "tire_balance",
        "road_force_balance_if_available",
        "wheel_alignment_4_wheel",
        "brake_vibration_diagnosis",
        "noise_vibration_harshness_nvh_diagnosis",
        "driveline_vibration_diagnosis",
      ],
      terms: ["shaking", "shake", "vibration", "misfire", "engine mount", "balance"],
    },
    vibration: {
      codes: [
        "noise_vibration_harshness_nvh_diagnosis",
        "brake_vibration_diagnosis",
        "driveline_vibration_diagnosis",
        "engine_mount_replacement",
        "tire_balance",
        "road_force_balance_if_available",
        "wheel_alignment_4_wheel",
        "wheel_bearing_replacement_front",
        "wheel_bearing_replacement_rear",
      ],
      terms: ["vibration", "shake", "wheel balance", "driveline", "bearing"],
    },
    brakenoise: {
      codes: [
        "brake_noise_diagnosis",
        "front_brake_pads_replacement",
        "rear_brake_pads_replacement",
        "front_brake_rotors_replacement",
        "rear_brake_rotors_replacement",
        "brake_caliper_replacement_each",
        "brake_hardware_kit_replacement",
        "brake_drum_service_if_applicable",
        "brake_shoe_replacement_if_applicable",
      ],
      terms: ["brake noise", "squeal", "grinding", "pads", "rotors", "caliper"],
    },
    grinding: {
      codes: [
        "brake_noise_diagnosis",
        "front_brake_pads_replacement",
        "rear_brake_pads_replacement",
        "front_brake_rotors_replacement",
        "rear_brake_rotors_replacement",
        "wheel_bearing_replacement_front",
        "wheel_bearing_replacement_rear",
      ],
      terms: ["grinding", "brake noise", "wheel bearing", "rotor"],
    },
    clunk: {
      codes: [
        "suspension_noise_diagnosis",
        "control_arm_replacement_each",
        "ball_joint_replacement_each",
        "sway_bar_link_replacement",
        "front_struts_replacement_pair",
        "rear_shocks_replacement_pair",
        "engine_mount_replacement",
      ],
      terms: ["clunk", "suspension noise", "control arm", "ball joint", "sway bar"],
    },
    squeak: {
      codes: [
        "suspension_noise_diagnosis",
        "brake_noise_diagnosis",
        "sway_bar_bushing_replacement",
        "control_arm_replacement_each",
        "front_struts_replacement_pair",
        "rear_shocks_replacement_pair",
        "belt_tensioner_replacement",
        "idler_pulley_replacement",
      ],
      terms: ["squeak", "squeal", "suspension noise", "brake noise", "belt noise"],
    },
    pulling: {
      codes: [
        "steering_pull_diagnosis",
        "wheel_alignment_2_wheel",
        "wheel_alignment_4_wheel",
        "tie_rod_end_replacement_each",
        "inner_tie_rod_replacement_each",
        "brake_caliper_replacement_each",
        "control_arm_replacement_each",
      ],
      terms: ["pulling", "pulls", "alignment", "steering pull", "caliper"],
    },
    wandering: {
      codes: [
        "steering_pull_diagnosis",
        "wheel_alignment_4_wheel",
        "tie_rod_end_replacement_each",
        "inner_tie_rod_replacement_each",
        "ball_joint_replacement_each",
        "control_arm_replacement_each",
        "steering_rack_replacement",
      ],
      terms: ["wandering", "loose steering", "alignment", "tie rod", "ball joint"],
    },
    acnotcold: {
      codes: [
        "a_c_performance_check",
        "a_c_system_diagnosis",
        "a_c_recharge",
        "evacuate_and_recharge",
        "a_c_leak_test_uv_dye_electronic",
        "a_c_compressor_replacement",
        "a_c_condenser_replacement",
        "a_c_pressure_switch_replacement",
      ],
      terms: ["ac not cold", "a c not cold", "air conditioning", "recharge", "compressor"],
    },
    weakheat: {
      codes: [
        "climate_control_diagnosis",
        "heater_core_replacement",
        "thermostat_replacement",
        "coolant_flush",
        "coolant_temperature_sensor_replacement",
        "heater_control_valve_replacement",
        "blend_door_actuator_replacement",
      ],
      terms: ["weak heat", "no heat", "heater", "heater core", "blend door", "thermostat"],
    },
    fluidleak: {
      codes: [
        "fluid_leak_diagnosis",
        "oil_leak_inspection",
        "coolant_leak_diagnosis",
        "transmission_leak_diagnosis",
        "brake_line_repair",
        "power_steering_hose_replacement",
        "fuel_leak_inspection",
        "a_c_line_repair_replacement",
      ],
      terms: ["fluid leak", "leak", "oil leak", "coolant leak", "transmission leak"],
    },
    oilleak: {
      codes: [
        "oil_leak_inspection",
        "fluid_leak_diagnosis",
        "valve_cover_gasket_replacement",
        "oil_pan_gasket_replacement",
        "oil_filter_housing_gasket_replacement",
        "rear_main_seal_replacement",
        "front_crank_seal_replacement",
        "camshaft_seal_replacement",
      ],
      terms: ["oil leak", "burning oil", "gasket", "seal"],
    },
    coolantleak: {
      codes: [
        "coolant_leak_diagnosis",
        "cooling_system_pressure_test",
        "radiator_replacement",
        "water_pump_replacement",
        "upper_radiator_hose_replacement",
        "lower_radiator_hose_replacement",
        "coolant_reservoir_replacement",
        "heater_hose_replacement_set",
      ],
      terms: ["coolant leak", "antifreeze", "pressure test", "radiator hose"],
    },
    burningsmell: {
      codes: [
        "fluid_leak_diagnosis",
        "oil_leak_inspection",
        "valve_cover_gasket_replacement",
        "exhaust_leak_repair",
        "brake_caliper_replacement_each",
        "electrical_diagnostic",
        "wiring_diagnosis",
      ],
      terms: ["burning smell", "burning oil", "burning rubber", "electrical smell"],
    },
    fuelsmell: {
      codes: [
        "fuel_leak_inspection",
        "fuel_line_repair_replacement",
        "evap_leak_test_smoke_test",
        "evap_purge_valve_replacement",
        "evap_vent_valve_replacement",
        "fuel_tank_replacement_if_applicable",
        "fuel_injector_seal_replacement",
      ],
      terms: ["fuel smell", "gas smell", "fuel leak", "evap", "gasoline"],
    },
  };

  function normalizeServiceSearch(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function compactServiceSearch(value) {
    return normalizeServiceSearch(value).replace(/\s+/g, "");
  }

  function serviceSearchText(service) {
    return [
      service.name,
      service.code,
      service.category,
      service.categoryName,
      service.description,
      service.summary,
      service.meta,
      Array.isArray(service.keywords) ? service.keywords.join(" ") : service.keywords,
      Array.isArray(service.aliases) ? service.aliases.join(" ") : service.aliases,
    ].join(" ");
  }

  function escapeServiceResultHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function getServiceHelperText(service) {
    const direct = String(service.summary || service.description || service.meta || "").trim();
    if (direct) return direct;

    const keywordText = Array.isArray(service.keywords)
      ? service.keywords.slice(0, 4).join(", ")
      : String(service.keywords || "").trim();
    if (keywordText) {
      return `Related to ${keywordText}.`;
    }

    const name = String(service.name || "").toLowerCase();
    const category = String(service.categoryName || service.category || "service").toLowerCase();
    if (name.includes("thermostat")) return "Common fix for overheating or temperature fluctuation.";
    if (name.includes("brake")) return "Useful when brake noise, vibration, or stopping performance points to this repair.";
    if (name.includes("battery")) return "Common starting point for no-start, weak crank, or low-voltage complaints.";
    if (name.includes("alternator")) return "Helps address battery light, charging problems, or repeated dead battery symptoms.";
    if (name.includes("spark") || name.includes("coil") || name.includes("misfire")) return "Common path for misfire, rough idle, or hesitation diagnosis.";
    if (name.includes("radiator") || name.includes("water pump") || name.includes("coolant")) return "Relevant when overheating, leaks, or cooling-system faults are suspected.";
    return `Relevant ${category} service when diagnosis points to this repair path.`;
  }

  function getServiceCategoryName(categoryKey) {
    const selectedOption = categoryEl?.querySelector(`option[value="${categoryKey}"]`);
    const knownCategory = serviceCategories.find((category) => category.key === categoryKey);
    return selectedOption?.textContent || knownCategory?.name || categoryKey || "";
  }

  function mapServiceSearchOption(service, categoryKey, categoryName) {
    return {
      code: service.code || "",
      name: service.name || service.code || "Service",
      category: service.category || categoryKey || "",
      categoryName: categoryName || getServiceCategoryName(categoryKey),
      description: service.description || "",
      summary: service.summary || "",
      meta: service.meta || "",
      keywords: service.keywords || "",
      aliases: service.aliases || "",
    };
  }

  function getServiceSearchVehicleKey() {
    const activeVehicle = typeof getActiveVehicle === "function" ? getActiveVehicle() : null;
    return [
      activeVehicle?.year || "",
      activeVehicle?.make || "",
      activeVehicle?.model || "",
    ].join("|");
  }

  async function ensureAllServiceOptions() {
    const vehicleKey = getServiceSearchVehicleKey();
    if (allServiceOptions.length && allServiceOptionsVehicleKey === vehicleKey) {
      return allServiceOptions;
    }

    if (!serviceCategories.length) {
      serviceCategories = await apiJSON("/api/categories");
    }

    const groupedServices = await Promise.all(
      serviceCategories.map(async (category) => {
        try {
          const categoryKey = category.key || "";
          const categoryName = category.name || categoryKey;
          const services = filterServicesForActiveVehicle(
            await apiJSON(`/api/services/${encodeURIComponent(categoryKey)}`)
          );
          return services.map((service) => mapServiceSearchOption(service, categoryKey, categoryName));
        } catch {
          return [];
        }
      })
    );

    const seenCodes = new Set();
    allServiceOptions = groupedServices
      .flat()
      .filter((service) => {
        const serviceKey = service.code || service.name;
        if (!serviceKey || seenCodes.has(serviceKey)) return false;
        seenCodes.add(serviceKey);
        return true;
      });
    allServiceOptionsVehicleKey = vehicleKey;
    return allServiceOptions;
  }

  function getServiceSearchCluster(query) {
    const normalizedQuery = normalizeServiceSearch(query);
    const compactQuery = compactServiceSearch(query);
    return SERVICE_SEARCH_CLUSTERS[compactQuery] || SERVICE_SEARCH_CLUSTERS[normalizedQuery] || null;
  }

  function serviceMatchesSearch(service, query) {
    const normalizedQuery = normalizeServiceSearch(query);
    const compactQuery = compactServiceSearch(query);
    const searchableText = serviceSearchText(service);
    const normalizedText = normalizeServiceSearch(searchableText);
    const compactText = compactServiceSearch(searchableText);

    if (!normalizedQuery) return false;
    if (normalizedText.includes(normalizedQuery) || compactText.includes(compactQuery)) {
      return true;
    }

    const cluster = getServiceSearchCluster(query);
    if (cluster) {
      const clusterCodes = Array.isArray(cluster.codes) ? cluster.codes : [];
      if (clusterCodes.includes(service.code)) {
        return true;
      }

      const clusterTerms = Array.isArray(cluster.terms) ? cluster.terms : [];
      if (clusterTerms.some((term) => {
        const normalizedTerm = normalizeServiceSearch(term);
        const compactTerm = compactServiceSearch(term);
        return normalizedText.includes(normalizedTerm) || compactText.includes(compactTerm);
      })) {
        return true;
      }
    }

    const aliasTerms = SERVICE_SEARCH_ALIASES[compactQuery] || SERVICE_SEARCH_ALIASES[normalizedQuery] || [];
    return aliasTerms.some((term) => {
      const normalizedTerm = normalizeServiceSearch(term);
      const compactTerm = compactServiceSearch(term);
      return normalizedText.includes(normalizedTerm) || compactText.includes(compactTerm);
    });
  }

  function hidePairedSuggestions() {
    pairedSuggestions?.classList.add("hidden");
    if (pairedSuggestionsList) pairedSuggestionsList.innerHTML = "";
  }

  function getPairedSuggestionConfig(lineItem) {
    const source = normalizeServiceSearch(`${lineItem?.serviceText || ""} ${lineItem?.serviceCode || ""}`);
    if (!source) return null;
    return COMMONLY_ADDED_TOGETHER.find((group) =>
      group.match.some((term) => source.includes(normalizeServiceSearch(term)))
    ) || null;
  }

  function findPairedServiceOption(suggestion, options) {
    const exactCode = String(suggestion.serviceCode || "").trim();
    if (exactCode) {
      const exact = options.find((service) => service.code === exactCode);
      if (exact) return exact;
    }

    const query = suggestion.query || suggestion.label || "";
    const normalizedQuery = normalizeServiceSearch(query);
    if (!normalizedQuery) return null;

    return options.find((service) => serviceMatchesSearch(service, query)) ||
      options.find((service) => {
        const text = normalizeServiceSearch(serviceSearchText(service));
        return text.includes(normalizedQuery);
      }) ||
      null;
  }

  async function refreshPairedSuggestions() {
    if (!pairedSuggestions || !pairedSuggestionsList) return;
    if (!lineItems.length) {
      hidePairedSuggestions();
      return;
    }

    const latestLineItem = lineItems[lineItems.length - 1];
    const config = getPairedSuggestionConfig(latestLineItem);
    if (!config) {
      hidePairedSuggestions();
      return;
    }

    let options = [];
    try {
      options = await ensureAllServiceOptions();
    } catch (err) {
      console.warn("Commonly added service lookup failed", err);
      hidePairedSuggestions();
      return;
    }

    const existingCodes = new Set(lineItems.map((it) => it.serviceCode).filter(Boolean));
    const seenCodes = new Set();
    const suggestions = config.suggestions
      .map((suggestion) => {
        const option = findPairedServiceOption(suggestion, options);
        return option ? { ...suggestion, option } : null;
      })
      .filter((suggestion) => {
        const code = suggestion?.option?.code;
        if (!code || existingCodes.has(code) || seenCodes.has(code)) return false;
        seenCodes.add(code);
        return true;
      })
      .slice(0, 3);

    if (!suggestions.length) {
      hidePairedSuggestions();
      return;
    }

    pairedSuggestionsList.innerHTML = suggestions.map(({ label, option }) => `
      <button
        type="button"
        class="tm-paired-suggestion"
        data-service-code="${escapeServiceResultHtml(option.code)}"
        data-service-category="${escapeServiceResultHtml(option.category || "")}"
      >
        <span>${escapeServiceResultHtml(label || option.name)}</span>
        <small>Select for quote</small>
      </button>
    `).join("");

    pairedSuggestions.classList.remove("hidden");
  }

  async function selectPairedSuggestion(serviceCode, categoryKey) {
    if (!serviceCode || !categoryEl || !serviceEl) return;

    if (!readyForNextService) {
      addLineBtn?.click();
    }

    if (categoryKey && categoryEl.value !== categoryKey) {
      categoryEl.value = categoryKey;
      await loadServices(categoryKey);
    }

    applyServiceSelection(serviceCode);
    await loadServiceMeta(serviceCode);
    readyForNextService = true;
    hidePairedSuggestions();
    updateEstimateButtonState();

    const serviceName = serviceEl.options[serviceEl.selectedIndex]?.textContent?.trim() || "Suggested job";
    setStatus("info", `${serviceName} selected. Review pricing, then add it to the quote.`);
  }

  async function selectQuickQuoteShortcut(shortcutKey) {
    const shortcut = QUICK_QUOTE_SHORTCUTS[shortcutKey];
    if (!shortcut || !categoryEl || !serviceEl) return;

    if (!readyForNextService) {
      addLineBtn?.click();
    }

    if (categoryEl.value !== shortcut.category) {
      categoryEl.value = shortcut.category;
      await loadServices(shortcut.category);
    }

    applyServiceSelection(shortcut.serviceCode);
    await loadServiceMeta(shortcut.serviceCode);
    readyForNextService = true;
    hidePairedSuggestions();
    updateEstimateButtonState();

    document.querySelectorAll(".tm-quick-quote").forEach((btn) => {
      btn.classList.toggle("is-selected", btn.dataset.quickQuote === shortcutKey);
    });

    const serviceName =
      serviceEl.options[serviceEl.selectedIndex]?.textContent?.trim() ||
      shortcut.label;

    trackClarity("service_selected", {
      source: "quick_quote",
      service_code: shortcut.serviceCode,
      category: shortcut.category,
      service_name: serviceName,
    });

    setStatus("info", `${serviceName} selected. Review pricing, then add it to the quote.`);
    document.querySelector(".tm-estimate-action-panel")?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
  }

  function renderServiceResults(query) {
    if (!serviceSearch || !serviceResults || serviceSearch.disabled) {
      hideServiceResults();
      return;
    }

    const normalizedQuery = normalizeServiceSearch(query);
    if (!normalizedQuery) {
      hideServiceResults();
      return;
    }

    const cluster = getServiceSearchCluster(normalizedQuery);
    const searchOptions = cluster && allServiceOptions.length ? allServiceOptions : serviceOptions;
    const seenServiceCodes = new Set();
    const filtered = searchOptions
      .filter((service) => {
        if (!serviceMatchesSearch(service, normalizedQuery)) return false;
        const serviceKey = service.code || service.name;
        if (seenServiceCodes.has(serviceKey)) return false;
        seenServiceCodes.add(serviceKey);
        return true;
      })
      .slice(0, 8);

    if (!filtered.length) {
      hideServiceResults();
      return;
    }

    serviceResults.innerHTML = filtered
      .map(
        (service) => {
          const helperText = escapeServiceResultHtml(getServiceHelperText(service));
          return `
          <button
            type="button"
            class="service-result-item"
            data-service-code="${service.code}"
            data-service-category="${service.category || ""}"
            style="
              display:block;
              width:100%;
              text-align:left;
              padding:12px 14px;
              background:#ffffff;
              color:#0f172a;
              border:none;
              border-bottom:1px solid #e5e7eb;
              cursor:pointer;
              font-size:16px;
            "
          >
            <span style="display:block; font-weight:700;">${escapeServiceResultHtml(service.name)}</span>
            <span class="tm-muted" style="display:block; margin-top:4px; font-size:12px; line-height:1.35;">${helperText}</span>
          </button>
        `;
        }
      )
      .join("");

    serviceResults.style.display = "block";
  }

  function resetServiceSearch({ placeholder = "Select category first...", disabled = true } = {}) {
    serviceOptions = [];
    if (!serviceSearch) return;
    serviceSearch.value = "";
    serviceSearch.placeholder = placeholder;
    serviceSearch.disabled = disabled;
    hideServiceResults();
  }

  function enableServiceSearch() {
    if (!serviceSearch) return;
    serviceSearch.disabled = false;
    serviceSearch.placeholder = "Search service...";
  }

  function applyServiceSelection(serviceCode) {
    if (!serviceEl) return;
    serviceEl.value = serviceCode;
    syncServiceSearchFromSelect();
    hideServiceResults();
    document.querySelectorAll(".tm-quick-quote").forEach((btn) => {
      const shortcut = QUICK_QUOTE_SHORTCUTS[btn.dataset.quickQuote || ""];
      btn.classList.toggle("is-selected", shortcut?.serviceCode === serviceCode);
    });
  }

  function getTimingSystemConfig(vehicle) {
    const make = String(vehicle?.make || "").trim().toUpperCase();
    const model = String(vehicle?.model || "").trim().toUpperCase();

    if (!make || !model) return null;
    return TIMING_SYSTEM_SUPPORT[make]?.[model] || null;
  }

  function filterServicesForActiveVehicle(svcs) {
    const services = Array.isArray(svcs) ? [...svcs] : [];
    const timingConfig = getTimingSystemConfig(getActiveVehicle());
    const hiddenTimingCodes = new Set([TIMING_SERVICE_CODES.timingChainLegacy]);

    if (!timingConfig) {
      hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingChainKit);
      return services.filter((service) => !hiddenTimingCodes.has(service.code));
    }

    if (timingConfig.type === "chain") {
      hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingBelt);
      return services.filter((service) => !hiddenTimingCodes.has(service.code));
    }

    if (timingConfig.type === "belt") {
      hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingChainKit);
      return services.filter((service) => !hiddenTimingCodes.has(service.code));
    }

    return services.filter((service) => !hiddenTimingCodes.has(service.code));
  }

  async function loadCategories() {
    if (!categoryEl) return;
    categoryEl.innerHTML = `<option value="">Select category…</option>`;
    const cats = await apiJSON("/api/categories");
    serviceCategories = cats;
    allServiceOptions = [];
    allServiceOptionsVehicleKey = "";
    for (const c of cats) {
      const opt = document.createElement("option");
      opt.value = c.key;
      opt.textContent = c.name;
      categoryEl.appendChild(opt);
    }
  }

  async function loadServices(categoryKey) {
    if (!serviceEl) return;

    resetServiceSearch();
    serviceEl.innerHTML = `<option value="">Select service…</option>`;
    serviceMeta = null;
    laborHoursTouched = false;

    if (!categoryKey) return;

    const svcs = filterServicesForActiveVehicle(
      await apiJSON(`/api/services/${encodeURIComponent(categoryKey)}`)
    );
    const categoryName = categoryEl?.options[categoryEl.selectedIndex]?.textContent || "";
    serviceOptions = svcs.map((s) => mapServiceSearchOption(s, categoryKey, categoryName));
    for (const s of svcs) {
      const opt = document.createElement("option");
      opt.value = s.code || "";
      opt.textContent = s.name || s.code || "Service";
      serviceEl.appendChild(opt);
    }
    enableServiceSearch();
  }

  async function loadServiceMeta(serviceCode) {
    serviceMeta = null;

    if (!serviceCode) {
      if (laborHoursRangeEl) laborHoursRangeEl.textContent = "";
      const confidenceEl = document.getElementById("laborConfidence");
      if (confidenceEl) {
        confidenceEl.textContent = "";
        confidenceEl.className = "labor-confidence";
      }
      return;
    }

    serviceMeta = await apiJSON(`/api/service/${encodeURIComponent(serviceCode)}`);

    updateLaborRangeUI();

    const mn = Number(serviceMeta?.labor_hours_min ?? 0);
    const mx = Number(serviceMeta?.labor_hours_max ?? 0);
    const midpoint = mx > 0 && mx >= mn ? (mn + mx) / 2 : 0;

    if ((!laborHoursTouched || !laborHoursEl.value) && midpoint > 0) {
      laborHoursEl.value = fmt1(midpoint);
    }

    const confidenceEl = document.getElementById("laborConfidence");
    if (confidenceEl) {
      let label = "";
      let className = "labor-confidence";

      if (midpoint > 0) {
        label = "Standard labor";
        className += " conf-typical";

        if (midpoint <= 1.5) {
          label = "Quick service";
          className = "labor-confidence conf-low";
        } else if (midpoint >= 3.5) {
          label = "Extended labor";
          className = "labor-confidence conf-high";
        }
      }

      confidenceEl.textContent = label;
      confidenceEl.className = className;
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

  document.querySelectorAll('input[name="wantSig"]').forEach((el) => {
    el.addEventListener("change", () => {
      setSigVisible(getWantSig() === "yes");
      if (getWantSig() === "no" && confirmMsg) {
        clearConfirmMessage();
      }
    });
  });

  function openConfirm() {
    if (!confirmModal) return;

    clearConfirmMessage();
    confirmModal.classList.remove("hidden");
    confirmModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");

    setSigVisible(getWantSig() === "yes");

    const listEl = document.getElementById("confirmServicesList");
    if (listEl) {
      const total = lineItems.reduce((sum, it) => sum + Number(it.estimate || 0), 0);

      listEl.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:10px;">
          ${lineItems.map(it => `
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
              <div>
                <div>${it.serviceText}</div>
                <div style="font-size:12px; opacity:.72; margin-top:2px;">
                  ${it.vehicleLabel || "No vehicle assigned"}
                </div>
              </div>
              <div style="font-weight:700; white-space:nowrap;">${money(it.estimate)}</div>
            </div>
          `).join("")}
          <div style="border-top:1px solid rgba(255,255,255,.15);padding-top:8px;display:flex;justify-content:space-between;">
            <div style="font-weight:800;">Grand Total</div>
            <div style="font-weight:900;">${money(total)}</div>
          </div>
        </div>
      `;
    }

    refreshQuotePreview();
    resizeSigCanvas();
  }

  function closeConfirm() {
    if (document.activeElement && confirmModal?.contains(document.activeElement)) {
      document.activeElement.blur();
    }

    confirmModal?.classList.add("hidden");
    confirmModal?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    clearConfirmMessage();

    quickEstimateBtn?.focus();
  }
  confirmBackdrop?.addEventListener("click", closeConfirm);
  confirmCloseBtn?.addEventListener("click", closeConfirm);

  customerNameEl?.addEventListener("input", refreshQuotePreview);
  notesEl?.addEventListener("input", refreshQuotePreview);
  pdfShowGeneratedDateChk?.addEventListener("change", refreshQuotePreview);
  pdfShowHourlyRateChk?.addEventListener("change", () => {
    renderLineItems();
    refreshQuotePreview();
  });
  pdfShowLaborColumnChk?.addEventListener("change", renderLineItems);
  pdfShowPartsColumnChk?.addEventListener("change", renderLineItems);
  pdfShowLaborBreakdownChk?.addEventListener("change", () => {
    if (!pdfShowLaborBreakdownChk.checked) {
      lineItems.forEach(it => { it.breakdownOpen = false; });
    }
    renderLineItems();
  });

  copyQuoteBtn?.addEventListener("click", async () => {
    try {
      const text = buildQuoteMessage();

      if (!text.trim()) {
        setConfirmMessage("info", "Nothing to copy yet.");
        return;
      }

      await navigator.clipboard.writeText(text);
      setConfirmMessage("ok", "Quote message copied.");
      setStatus("ok", "Quote message copied.");
    } catch (e) {
      setConfirmMessage("error", "Copy failed. Try selecting the text manually.");
      setStatus("error", `Copy failed: ${e.message}`);
    }
  });

  emailQuoteBtn?.addEventListener("click", emailEstimate);

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && confirmModal && !confirmModal.classList.contains("hidden")) closeConfirm();
  });

  document.querySelectorAll('input, select, textarea').forEach((el) => {
  el.addEventListener('change', () => {
    if (window.innerWidth < 768) el.blur();
  });

  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && window.innerWidth < 768) {
      el.blur();
    }
  });
});
 // ---- Signature pad ----
  let isDrawing = false;
  let lastX = 0, lastY = 0;
  let lastMidX = 0;
  let lastMidY = 0;

  function signatureInkColor() {
    return "#0f172a";
  }

  function signaturePadBg() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "rgba(255,255,255,0.06)"
      : "#f8fafc";
  }


  function clearSignatureCanvas() {
    if (!sigCanvas) return;

    const ctx = sigCanvas.getContext("2d");

    ctx.clearRect(0, 0, sigCanvas.width, sigCanvas.height);

    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = signatureInkColor();
  }

  function resizeSigCanvas() {
    if (!sigCanvas) return;

    const rect = sigCanvas.getBoundingClientRect();

    const prevData = sigCanvas.toDataURL(); // preserve drawing

    sigCanvas.width = Math.round(rect.width);
    sigCanvas.height = Math.round(rect.height);

    sigCtx = sigCanvas.getContext("2d");

    sigCtx.lineWidth = 2;
    sigCtx.lineCap = "round";
    sigCtx.lineJoin = "round";
    sigCtx.strokeStyle = signatureInkColor();

    // restore previous drawing
    const img = new Image();
    img.onload = () => sigCtx.drawImage(img, 0, 0);
    img.src = prevData;
  }

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    signatureDataUrl = null;
    clearSignatureCanvas();
  });

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
    const point = e.touches ? e.touches[0] : e;

    return {
      x: point.clientX - rect.left,
      y: point.clientY - rect.top,
    };
  }

  function startDraw(e) {
    if (!sigCanvas || !sigCtx) return;
    e.preventDefault();

    sigCtx.strokeStyle = signatureInkColor();
    sigCtx.lineWidth = 2.5;
    sigCtx.lineCap = "round";
    sigCtx.lineJoin = "round";

    isDrawing = true;

    const p = getCanvasPos(e);
    lastX = p.x;
    lastY = p.y;
    lastMidX = p.x;
    lastMidY = p.y;
  }
  function draw(e) {
    if (!isDrawing || !sigCtx) return;
    e.preventDefault();

    const p = getCanvasPos(e);

    const midX = (lastX + p.x) / 2;
    const midY = (lastY + p.y) / 2;

    sigCtx.beginPath();
    sigCtx.moveTo(lastMidX, lastMidY);
    sigCtx.quadraticCurveTo(lastX, lastY, midX, midY);
    sigCtx.stroke();

    lastX = p.x;
    lastY = p.y;
    lastMidX = midX;
    lastMidY = midY;
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
    const activeVehicle = getActiveVehicle() || {};

    return {
      year: Number(activeVehicle.year || 0),
      make: (activeVehicle.make || "").trim(),
      model: (activeVehicle.model || "").trim(),
      category: (categoryEl?.value || "").trim() || null,
      serviceCode: (serviceEl?.value || "").trim() || null,

      pricingMode: getPricingMode(),
      flatRatePrice: pricingInputNumber(flatRatePriceEl),
      travelFee: pricingInputNumber(travelFeeEl),
      laborHours: pricingInputNumber(laborHoursEl),
      partsPrice: pricingInputNumber(partsPriceEl),
      laborRate: pricingInputNumber(laborRateEl),
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
    renderEstimateTotalBar();
    if (!lineItemsWrap || !lineItemsList) return;

    lineItemsWrap.classList.toggle("hidden", lineItems.length === 0);
    const outputOptions = getCustomerOutputOptions();

    lineItemsList.innerHTML = lineItems
      .map((it, idx) => {
        if (!it.id) {
          it.id = createLineItemId();
        }
        const est = it.estimate != null ? money(it.estimate) : "—";

        const travelLabel = Number(it.travelFee || 0) > 0
          ? `Travel: ${money(it.travelFee)}`
          : "";

        const pricingMeta = it.pricingMode === "flat"
          ? [
              `Flat Rate: ${money(it.flatRatePrice || 0)}`,
              `Parts: ${money(it.partsPrice || 0)}`,
            ]
          : [
              `Labor Hours: ${Number(it.laborHours || 0).toFixed(1)}h`,
              `Parts: ${money(it.partsPrice || 0)}`,
            ];

        if (outputOptions.showHourlyRate && it.pricingMode !== "flat") {
          pricingMeta.push(`Rate: $${Number(it.laborRate || 0).toFixed(0)}/hr`);
        }

        if (travelLabel) {
          pricingMeta.push(travelLabel);
        }

        const hasBreakdown =
          outputOptions.showDetailedLaborBreakdown &&
          it.laborBreakdown &&
          Array.isArray(it.laborBreakdown.steps) &&
          it.laborBreakdown.steps.length > 0;
        const lineItemId = it.id || "";

        return `
          <div class="tm-service-card" data-idx="${idx}" data-line-item-id="${lineItemId}">
            <div class="tm-service-head">
              <div class="tm-service-head-main">
                <div class="tm-service-title">${it.serviceText || "Service"}</div>
                <div class="tm-service-vehicle">${it.vehicleLabel || "Vehicle 1"}</div>
                <div class="tm-service-meta">
                  ${pricingMeta.map(label => `<span>${label}</span>`).join("")}
                </div>
              </div>

              <div class="tm-service-estimate">
                <span class="tm-service-estimate-label">Estimate</span>
                <strong>${est}</strong>
              </div>
            </div>

            <div class="tm-service-actions">
              ${hasBreakdown ? `
                <button
                  type="button"
                  class="tm-btn tm-btn-secondary tm-service-toggle"
                  data-action="toggle-breakdown"
                  data-line-item-id="${lineItemId}"
                >
                  ${it.breakdownOpen ? "Hide labor breakdown" : "Show labor breakdown"}
                </button>
              ` : ""}

              <button type="button" class="tm-btn tm-btn-secondary" data-action="edit-line" data-line-item-id="${lineItemId}">
                Edit Line
              </button>

              <button type="button" class="tm-btn tm-btn-danger" data-action="remove" data-line-item-id="${lineItemId}">
                Remove
              </button>
            </div>

            ${hasBreakdown && it.breakdownOpen ? `
              <div class="tm-labor-panel">
                <div class="tm-labor-range">
                  Typical range: ${Number(it.laborBreakdown.labor_hours?.min || 0).toFixed(1)} - ${Number(it.laborBreakdown.labor_hours?.max || 0).toFixed(1)} hrs
                </div>

                <div class="tm-labor-rows">
                  ${it.laborBreakdown.steps.map(step => `
                    <div class="tm-labor-row">
                      <span>${step.label}</span>
                      <strong>${Number(step.hours || 0).toFixed(1)} hr</strong>
                    </div>
                  `).join("")}
                </div>

                <p class="tm-labor-note">
                  Labor distribution is a guide. Actual time may vary depending on vehicle condition and access.
                </p>
              </div>
            ` : ""}
          </div>
        `;
      })
      .join("");

    syncEstimateMeta();
    syncLineItemsToVehicle();
    updateEstimateButtonState();
  }

  function updateEstimateButtonState() {
    if (!estimateBtn) return;

    const activeVehicle = getActiveVehicle() || {};
    const hasBasics = !!(activeVehicle.year && activeVehicle.make && activeVehicle.model);
    const hasSelection = !!(categoryEl?.value && serviceEl?.value);
    const isEditingSavedLine = !!activeEditingLineId;

    // --- Add Service button label (dynamic) ---
    if (addLineBtn) {
      // If user already added a service (locked state), guide them to add another
      addLineBtn.textContent = "Add Another Job";
      addLineBtn.hidden = isEditingSavedLine || (lineItems.length === 0 && readyForNextService);
    }

    estimateBtn.disabled = isEditingSavedLine ? false : !(hasBasics && hasSelection && readyForNextService);
    estimateBtn.textContent = isEditingSavedLine
      ? "Save Line Changes"
      : editingLineItem ? "Update Quote Line" : "Add Service to Quote";


// Hint text: when locked, explain the flow
if (addServiceHint) {
  addServiceHint.hidden = isEditingSavedLine ? false : !!readyForNextService;
  if (isEditingSavedLine) {
    addServiceHint.textContent = "Editing saved line. Update pricing, then save changes.";
  } else {
    addServiceHint.textContent = "Tap Add Service Line to keep building this quote.";
  }
}
if (getEstimateHint) {
  // Show hint only when button is disabled
  getEstimateHint.hidden = isEditingSavedLine || !estimateBtn.disabled;

  if (!getEstimateHint.hidden) {
    getEstimateHint.textContent = !hasBasics
      ? "Set the vehicle first."
      : !hasSelection
        ? "Choose the repair job first."
        : "Tap Add Another Job, then choose the next repair.";
  }
}

    if (workflowStepText) {
      workflowStepText.textContent = isEditingSavedLine
        ? "Editing saved line. Update pricing, then save changes."
        : !hasBasics
        ? "Set the vehicle before pricing the job."
        : !hasSelection
          ? "Choose the repair job for this vehicle."
          : !readyForNextService
            ? "Job added. Add another job or review the quote total."
            : "Review pricing, then add this job to the quote.";
    }

    // Add Another Service enabled ONLY after a service has been added
    if (addLineBtn) addLineBtn.disabled = isEditingSavedLine || readyForNextService;

    // keep status helpful, but don't spam over error messages
    if (isEditingSavedLine) setStatus("info", "Editing saved line. Update pricing, then save changes.");
    else if (!hasBasics) setStatus("info", "Set the vehicle before pricing the job.");
    else if (!hasSelection) setStatus("info", "Choose a category and repair job.");
    else if (!readyForNextService) setStatus("info", "Job added. Add another job or review the quote.");
    else setStatus("info", "Review pricing, then add this job to the quote.");
  }

  // ---- Add Service to Estimate FIRST ----
  estimateBtn?.addEventListener("click", async () => {

    // Google Analytics event
    if (typeof gtag === "function") {
      gtag("event", "get_estimate_clicked", {
        event_category: "engagement",
        event_label: "Estimator Tool"
      });
    }

    if (activeEditingLineId) {
      const it = getLineItemById(activeEditingLineId);
      if (!it) {
        console.warn("Save Line Changes ignored: active line item not found", {
          activeEditingLineId,
        });
        activeEditingLineId = null;
        updateEstimateButtonState();
        return;
      }

      Object.assign(it, buildPricingSnapshotFromControls());
      setStatus("info", `Saving changes to ${it.serviceText}...`);

      try {
        await recalculateLineItemFromSnapshot(it, "line_item_save_changes");
        activeEditingLineId = null;
        readyForNextService = false;
        renderLineItems();
        void refreshPairedSuggestions();
        updateEstimateButtonState();
        setStatus("ok", `${it.serviceText}: ${money(it.estimate)}. Line updated.`);
      } catch (e) {
        setStatus("error", `Save line failed: ${e.message}`);
      }
      return;
    }

    if (!readyForNextService) return;

    const activeVehicle = getActiveVehicle();
    if (!(activeVehicle?.year && activeVehicle?.make && activeVehicle?.model)) {
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

    if (!activeVehicle) {
      setStatus("error", "Select a vehicle first.");
      return;
    }

    const pricingSnapshot = buildPricingSnapshotFromControls();
    const it = {
      id: createLineItemId(),
      vehicleId: activeVehicle.id,
      vehicleLabel: getVehicleLabel(activeVehicle),
      vehicleYear: activeVehicle.year || "",
      vehicleMake: activeVehicle.make || "",
      vehicleModel: activeVehicle.model || "",
      serviceCode: editingLineItem ? editingLineItem.serviceCode : serviceEl.value,
      serviceText,
      ...pricingSnapshot,
      notes: (notesEl?.value || "").trim() || null,
      estimate: null,
    };

    // add the card immediately
    lineItems.push(it);
    renderLineItems();

    setStatus("info", `Pricing: ${serviceText}…`);

    try {
      if (it.pricingMode === "flat") {
        it.estimate = calcLineItemEstimate(it);
        editingLineItem = null;
        lastEstimate = null;

        renderLineItems();
        trackClarity("estimate_generated", {
          source: "estimator",
          action: "get_estimate",
          service_code: it.serviceCode,
          service_name: it.serviceText,
          estimate_total: Number(it.estimate || 0)
        });
        setStatus("ok", `${it.serviceText}: ${money(it.estimate)}. Add another job or review the quote.`);

        readyForNextService = false;
        updateEstimateButtonState();
        void refreshPairedSuggestions();
        return;
      }

      const req = buildLineItemEstimateRequest(it);

      const res = await apiJSON("/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
      });

      it.estimate = calcLineItemEstimate(it);
      it.laborBreakdown = res.labor_breakdown || null;
      it.breakdownOpen = false;

      if (res.labor_breakdown?.labor_hours) {
        const lh = res.labor_breakdown.labor_hours;

        if (laborHoursRangeEl) {
          laborHoursRangeEl.textContent = `${lh.min.toFixed(1)} - ${lh.max.toFixed(1)} hrs`;
        }
      }

      // ===== LABOR HOURS UI =====
      if (res.labor_breakdown) {
        const lh = res.labor_breakdown.labor_hours;

        if (laborHoursRangeEl) {
          laborHoursRangeEl.textContent = `${lh.min.toFixed(1)} - ${lh.max.toFixed(1)} hrs`;
        }
      }

      // ===== LABOR BREAKDOWN =====
      
      editingLineItem = null;
      lastEstimate = { req, res };

      renderLineItems();
      trackClarity("estimate_generated", {
        source: "estimator",
        action: "get_estimate",
        service_code: it.serviceCode,
        service_name: it.serviceText,
        estimate_total: Number(it.estimate || 0)
      });
      setStatus("ok", `${it.serviceText}: ${money(it.estimate)}. Add another job or review the quote.`);

      readyForNextService = false;
      updateEstimateButtonState();
      void refreshPairedSuggestions();
    } catch (e) {
      const currentIndex = lineItems.indexOf(it);
      if (currentIndex >= 0) {
        lineItems.splice(currentIndex, 1);
      }
      renderLineItems();
      void refreshPairedSuggestions();

      readyForNextService = true;
      updateEstimateButtonState();

      setStatus("error", `Estimate failed: ${e.message}`);
    }
  });

  // Add Another Service
  addLineBtn?.addEventListener("click", () => {
    // Clear selections
    activeEditingLineId = null;
    categoryEl.value = "";
    serviceEl.value = "";
    serviceEl.innerHTML = `<option value="">Select service…</option>`;
    resetServiceSearch();
    document.querySelectorAll(".tm-quick-quote").forEach((btn) => btn.classList.remove("is-selected"));
    hidePairedSuggestions();

    serviceMeta = null;
    if (laborHoursRangeEl) laborHoursRangeEl.textContent = "";

    laborHoursTouched = false;
    laborHoursEl.value = "0";
    partsPriceEl.value = "0";
    if (notesEl) notesEl.value = "";

    if (pricingModeEl) pricingModeEl.value = "hourly";
    if (flatRatePriceEl) flatRatePriceEl.value = "0";
    if (travelFeeEl) travelFeeEl.value = "0";
    togglePricingModeUI();

    // Unlock Add Service to Estimate
    readyForNextService = true;
    updateEstimateButtonState();

    setStatus("info", "Choose the next repair job, then add it to the quote.");
  });

  // IMPORTANT: this must be async because we use await inside
  lineItemsList?.addEventListener("click", async (e) => {
    const btn = e.target?.closest?.("button[data-action]");
    if (!btn) return;
    e.preventDefault();

    const row = btn.closest(".tm-service-card");
    const lineItemId = btn.dataset.lineItemId || row?.dataset?.lineItemId || "";
    const idx = lineItems.findIndex((candidate) => candidate.id === lineItemId);
    if (idx < 0) {
      console.warn("Quoted service action ignored: line item id not found", {
        action: btn.dataset.action || "",
        lineItemId,
      });
      return;
    }

    const action = btn.dataset.action;
    const it = lineItems[idx];
    if (!it) {
      console.warn("Quoted service action ignored: line item missing", {
        action,
        lineItemId,
      });
      return;
    }

    // =========================
    // REMOVE (FIRST)
    // =========================
    if (action === "remove") {
      if (activeEditingLineId === it.id) {
        activeEditingLineId = null;
      }
      lineItems.splice(idx, 1);
      renderLineItems();
      void refreshPairedSuggestions();
      return;
    }

    if (action === "toggle-breakdown") {
      it.breakdownOpen = !it.breakdownOpen;
      renderLineItems();
      return;
    }

    if (action === "edit-line") {
      activeEditingLineId = it.id;
      loadPricingSnapshotIntoControls(it);
      updateEstimateButtonState();
      document.querySelector(".pricing-controls")?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
      setStatus("info", "Editing saved line. Update pricing, then save changes.");
      return;
    }

    // =========================
    // ESTIMATE
    // =========================
    if (action === "estimate") {
      if (!(it.vehicleYear && it.vehicleMake && it.vehicleModel)) {
        setStatus("error", "Assigned vehicle is missing year, make, or model.");
        return;
      }

      renderLineItems();
      setStatus("info", `Recalculating ${it.serviceText}…`);

      try {
        if (it.pricingMode === "flat") {
          it.estimate = calcLineItemEstimate(it);
          renderLineItems();
          trackClarity("estimate_generated", {
            source: "estimator",
            action: "line_item_recalculate",
            service_code: it.serviceCode,
            service_name: it.serviceText,
            estimate_total: Number(it.estimate || 0)
          });
          setStatus("ok", `${it.serviceText}: ${money(it.estimate)}`);
          return;
        }

        // Card recalculation must use the clicked line item's saved pricing only.
        // Do not read global Pricing Controls here; those are for the next draft service.
        const req = buildLineItemEstimateRequest(it);
        const res = await apiJSON("/estimate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(req),
        });

        // ✅ CORRECT PLACE
        it.laborBreakdown = res.labor_breakdown || null;
        it.breakdownOpen = false;

        it.estimate = calcLineItemEstimate(it);

        renderLineItems();
        trackClarity("estimate_generated", {
          source: "estimator",
          action: "line_item_recalculate",
          service_code: it.serviceCode,
          service_name: it.serviceText,
          estimate_total: Number(it.estimate || 0)
        });
        setStatus("ok", `${it.serviceText}: ${money(it.estimate)}`);

      } catch (e) {
        setStatus("error", `Estimate failed: ${e.message}`);
      }
      return;
    }

    console.warn("Quoted service action ignored: unknown action", {
      action,
      lineItemId,
    });
  });

  let isGeneratingCustomerPdf = false;

  // Confirm Add = finalize signature and generate PDF
  async function handleGenerateCustomerPdf(e) {
    e?.preventDefault();

    if (isGeneratingCustomerPdf) return;
    isGeneratingCustomerPdf = true;

    clearConfirmMessage();

    try {
      if (!lineItems.length) {
        setConfirmMessage("error", "Add at least one quoted service before generating the PDF.");
        return;
      }

      const wantSig = getWantSig();

      // Signature required?
      if (wantSig === "yes") {
        try {
          if (!sigCanvas || canvasIsBlank()) {
            setConfirmMessage("error", "Customer signature required before generating PDF.");
            return;
          }

          // Export signature directly from the live canvas with transparent background
          signatureDataUrl = sigCanvas.toDataURL("image/png");
        } catch (err) {
          console.warn("Signature validation failed", err);
          setConfirmMessage("error", "Customer signature required before generating PDF.");
          return;
        }
      } 
      
      else {
        signatureDataUrl = null;
      }

      // Multi-line PDF uses lineItems; no need to call /estimate here.
      if (!lineItems.length) {
        setConfirmMessage("error", "Add at least one quoted service before generating the PDF.");
        return;
      }
      const missing = lineItems.some(it => it.estimate == null);
      if (missing) {
        setConfirmMessage("error", "Some quoted services are missing prices. Review pricing before generating the PDF.");
        return;
      }
      
      // Generate PDF
      setStatus("info", "Preparing customer PDF...");

      const activeVehicle = getActiveVehicle() || {};
      const outputOptions = getCustomerOutputOptions();

      const pdfResponse = await fetch("/estimate/pdf_multi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          year: Number(activeVehicle.year || 0),
          make: String(activeVehicle.make || "").trim(),
          model: String(activeVehicle.model || "").trim(),
          notes: (notesEl?.value || "").trim() || null,
          customerName: (customerNameEl?.value || "").trim() || null,
          customerPhone: (customerPhoneEl?.value || "").trim() || null,
          customerAgrees: !!customerAgreesChk?.checked,
          signatureDataUrl,
          showGeneratedDate: outputOptions.showGeneratedDate,
          showHourlyRate: outputOptions.showHourlyRate,
          showLaborColumn: outputOptions.showLaborColumn,
          showPartsColumn: outputOptions.showPartsColumn,
          showDetailedLaborBreakdown: outputOptions.showDetailedLaborBreakdown,
          lineItems: lineItems.map((it) => ({
            serviceCode: it.serviceCode,
            serviceText: it.serviceText,
            pricingMode: it.pricingMode,
            flatRatePrice: Number(it.flatRatePrice || 0),
            laborHours: Number(it.laborHours || 0),
            partsPrice: Number(it.partsPrice || 0),
            laborRate: Number(it.laborRate || 0),
            estimate: it.estimate != null ? Number(it.estimate) : null,
            laborBreakdown: it.laborBreakdown || null,
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

      // Show PDF immediately so user sees it
      window.open(pdfUrl, "_blank");

      // Also trigger download as backup
      const a = document.createElement("a");
      a.href = pdfUrl;
      a.download = "torquemech_estimate.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();

      setStatus("ok", "Customer PDF ready.");
      trackClarity("pdf_generated", {
        source: "estimator",
        state: "pdf_success",
        service_count: lineItems.length,
        estimate_total: lineItems.reduce((sum, it) => sum + Number(it.estimate || 0), 0)
      });

      if (confirmMsg) {
        confirmMsg.dataset.kind = "ok";
        confirmMsg.innerHTML = `
          Your customer PDF is ready.<br>
          Download, open, or share it with the customer.<br>
          <a href="${pdfUrl}" download="torquemech_estimate.pdf">Download PDF</a>
          &nbsp;|&nbsp;
          <a href="${pdfUrl}" target="_blank" rel="noopener">Open PDF</a>
          <div class="tm-card" style="margin-top:12px; padding:12px 14px;" aria-label="TorqueMech Pro PDF preview">
            <div style="font-weight:800;">Make this estimate customer-ready with your shop branding.</div>
            <div style="display:flex; flex-wrap:wrap; gap:10px; margin-top:10px;">
              <a class="tm-btn tm-btn-secondary" href="/shop-profile/pdf-preview" target="_blank" rel="noopener">Preview Pro PDF</a>
              <a class="tm-btn tm-btn-ghost" href="/shop-profile">Shop Profile</a>
            </div>
          </div>
        `;
      }

      // Do NOT auto-close immediately
      setTimeout(() => URL.revokeObjectURL(pdfUrl), 60000);

      setStatus("ok", "Customer PDF downloaded.");
      closeConfirm();

    } catch (e) {
      console.error("PDF generation failed", e);
      setStatus("error", "Unable to generate PDF. Please try again.");
      setConfirmMessage("error", "Unable to generate PDF. Please try again.");
    } finally {
      isGeneratingCustomerPdf = false;
    }
  }

  document.addEventListener("click", (e) => {
    const trigger = e.target?.closest?.("#confirmAddBtn");
    if (!trigger) return;
    handleGenerateCustomerPdf(e);
  });

  pairedSuggestionsList?.addEventListener("click", async (e) => {
    const btn = e.target?.closest?.(".tm-paired-suggestion[data-service-code]");
    if (!btn) return;

    btn.disabled = true;
    try {
      await selectPairedSuggestion(btn.dataset.serviceCode || "", btn.dataset.serviceCategory || "");
    } catch (err) {
      console.warn("Commonly added service selection failed", err);
      setStatus("error", "Unable to select that related job. Choose it from the service list instead.");
    } finally {
      btn.disabled = false;
    }
  });

  document.querySelector(".tm-quick-quotes")?.addEventListener("click", async (e) => {
    const btn = e.target?.closest?.(".tm-quick-quote[data-quick-quote]");
    if (!btn) return;

    btn.disabled = true;
    try {
      await selectQuickQuoteShortcut(btn.dataset.quickQuote || "");
    } catch (err) {
      console.warn("Quick Quote selection failed", err);
      setStatus("error", "Unable to select that quick quote. Choose it from the service list instead.");
    } finally {
      btn.disabled = false;
    }
  });

  // ---- Clear fields (Hard reset) ----
  clearBtn?.addEventListener("click", async () => {
    try {
      closeConfirm();
    } catch (_) {}

    activeEditingLineId = null;

    estimateState = {
      customer: {
        name: "",
        phone: "",
        email: ""
      },
      activeVehicleId: "veh_1",
      vehicles: [
        {
          id: "veh_1",
          year: "",
          make: "",
          model: "",
          services: []
        }
      ]
    };

    window.estimateState = estimateState;

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
    if (vinDecodedMeta) vinDecodedMeta.textContent = "";

    // service
    if (categoryEl) categoryEl.value = "";
    if (serviceEl) serviceEl.innerHTML = `<option value="">Select service…</option>`;
    resetServiceSearch();
    document.querySelectorAll(".tm-quick-quote").forEach((btn) => btn.classList.remove("is-selected"));

    // inputs
    if (laborHoursEl) laborHoursEl.value = "0";
    if (partsPriceEl) partsPriceEl.value = "0";
    if (laborRateEl) laborRateEl.value = "90";
    if (pricingModeEl) pricingModeEl.value = "hourly";
    if (flatRatePriceEl) flatRatePriceEl.value = "0";
    if (travelFeeEl) travelFeeEl.value = "0";
    togglePricingModeUI();
    if (notesEl) notesEl.value = "";
    if (customerNameEl) customerNameEl.value = "";
    if (customerPhoneEl) customerPhoneEl.value = "";

    // signature
    const wantYes = document.querySelector('input[name="wantSig"][value="yes"]');
    if (wantYes) wantYes.checked = true;
    if (customerAgreesChk) customerAgreesChk.checked = true;
    setSigVisible(false);
    clearSignatureCanvas();
    clearConfirmMessage();
    if (quotePreviewEl) quotePreviewEl.value = "";
    if (draftsSelect) draftsSelect.value = "";
    const confirmServicesList = document.getElementById("confirmServicesList");
    if (confirmServicesList) confirmServicesList.textContent = "—";

    // UI blocks
    if (lineItemsList) lineItemsList.innerHTML = "";
    lineItemsWrap?.classList.add("hidden");
    hidePairedSuggestions();

    estimatePreview?.classList.add("hidden");
    if (previewTotalText) previewTotalText.textContent = "—";
    if (previewSubText) previewSubText.textContent = "";
    renderEstimateTotalBar();

    // reload core dropdown data
    try {
      await loadCategories();
      await renderVehicles();
      renderActiveVehicleBanner();
      await applyObdFromQuery();
      renderEstimateTotalBar();
    } catch (_) {}

    refreshQuotePreview();
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
      if (vinDecodedMeta) {
        const metaBits = [res.engine, res.trim].filter(Boolean);
        vinDecodedMeta.textContent = metaBits.length
          ? `Detected: ${metaBits.join(" • ")}`
          : "";
      }

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
      syncServiceSearchFromSelect();
      await loadServiceMeta(serviceEl.value);
      updateEstimateButtonState();
    } catch (e) {
      setStatus("error", `Service detail failed: ${e.message}`);
    }
  });

  serviceSearch?.addEventListener("input", async () => {
    if (!serviceEl) return;

    const searchValue = serviceSearch.value.trim();

    clearTimeout(searchDebounceTimer);

    if (searchValue.length >= 2) {
      searchDebounceTimer = setTimeout(() => {
        trackClarity("search_submit", { query: searchValue });
      }, 500); // 500ms delay
    }

    serviceEl.value = "";

    if (getServiceSearchCluster(serviceSearch.value)) {
      await ensureAllServiceOptions();
    }

    renderServiceResults(serviceSearch.value);
    await loadServiceMeta("");
    updateEstimateButtonState();
  });

  serviceSearch?.addEventListener("focus", async () => {
    if (serviceSearch.value.trim()) {
      if (getServiceSearchCluster(serviceSearch.value)) {
        await ensureAllServiceOptions();
      }
      renderServiceResults(serviceSearch.value);
    }
  });

  serviceSearch?.addEventListener("blur", () => {
    setTimeout(hideServiceResults, 150);
  });

  serviceResults?.addEventListener("click", async (event) => {
    const resultButton = event.target.closest(".service-result-item");
    if (!resultButton) return;
    const serviceCode = resultButton.dataset.serviceCode || "";
    const serviceCategory = resultButton.dataset.serviceCategory || "";
    if (serviceCategory && categoryEl && categoryEl.value !== serviceCategory) {
      categoryEl.value = serviceCategory;
      await loadServices(serviceCategory);
    }
    applyServiceSelection(serviceCode);
    trackClarity("service_selected", {
      service_code: serviceCode,
      category: serviceCategory || categoryEl?.value || "",
      service_name: serviceEl?.options?.[serviceEl.selectedIndex]?.textContent?.trim() || ""
    });
    await loadServiceMeta(serviceEl.value);
    updateEstimateButtonState();
  });

  serviceEl?.addEventListener("change", () => {
    trackClarity("service_selected", {
      service_code: serviceEl.value || "",
      category: categoryEl?.value || "",
      service_name: serviceEl.options?.[serviceEl.selectedIndex]?.textContent?.trim() || ""
    });
  });

  function syncTopVehicleToState() {
    const v = estimateState.vehicles[0];
    if (!v) return;

    v.year = yearEl?.value || "";
    v.make = makeEl?.value || "";
    v.model = modelEl?.value || "";

    window.estimateState = estimateState;
  }

  yearEl?.addEventListener("change", syncTopVehicleToState);
  makeEl?.addEventListener("change", syncTopVehicleToState);
  modelEl?.addEventListener("change", syncTopVehicleToState);

  yearEl?.addEventListener("change", async () => {
  syncTopVehicleToState();

  if (!makeEl?.value) return;

  try {
    modelEl.innerHTML = `<option value="">Loading models…</option>`;
    modelEl.value = "";
    await loadModels(makeEl.value);
    updateEstimateButtonState();
  } catch (e) {
    setStatus("error", `Models failed: ${e.message}`);
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
  copySavedEstimateLinkBtn?.addEventListener("click", async () => {
    if (!lastSavedEstimateLink) return;

    try {
      await navigator.clipboard.writeText(lastSavedEstimateLink);
      if (draftsMsg) draftsMsg.textContent = "Saved estimate link copied.";
      setStatus("ok", "Saved estimate link copied.");
    } catch (e) {
      if (draftsMsg) draftsMsg.textContent = "Copy failed. Select the link manually.";
      setStatus("error", `Copy failed: ${e.message}`);
    }
  });
  openSavedEstimateBtn?.addEventListener("click", () => {
    if (lastSavedEstimateLink) {
      window.location.href = lastSavedEstimateLink;
    }
  });
  downloadSavedEstimatePdfBtn?.addEventListener("click", () => {
    if (!lineItems.length) {
      setStatus("error", "Load or build an estimate before downloading a PDF.");
      return;
    }

    openConfirm();
    setConfirmMessage("info", "Review the saved quote, then generate the customer PDF.");
  });
  sharedDownloadPdfBtn?.addEventListener("click", () => {
    if (!lineItems.length) {
      setStatus("error", "Load or build an estimate before downloading a PDF.");
      return;
    }

    openConfirm();
    setConfirmMessage("info", "Review the shared quote, then generate the customer PDF.");
  });

  addVehicleBtn?.addEventListener("click", () => {
    addVehicleCard();
  });

  customerNameEl?.addEventListener("input", () => {
    syncEstimateMeta();
  });

  customerPhoneEl?.addEventListener("input", () => {
    syncEstimateMeta();
  });

  function addVehicleCard() {
    const activeVehicleId = estimateState.activeVehicleId || estimateState.vehicles[0]?.id || "";
    const activeYearSelect = activeVehicleId
      ? document.querySelector(`.vehicle-year[data-vehicle-id="${activeVehicleId}"]`)
      : document.querySelector(".vehicle-year");

    if (activeYearSelect instanceof HTMLElement) {
      activeYearSelect.scrollIntoView({ behavior: "smooth", block: "center" });
      activeYearSelect.focus();
    }

    setStatus("info", "This quote uses one vehicle. Update the vehicle details below.");
  }

  function removeVehicleCard(vehicleId) {
    if (estimateState.vehicles.length === 1) return;

    estimateState.vehicles = estimateState.vehicles.filter(v => v.id !== vehicleId);
    lineItems = lineItems.filter(it => it.vehicleId !== vehicleId);

    if (estimateState.activeVehicleId === vehicleId) {
      estimateState.activeVehicleId = estimateState.vehicles[0]?.id || null;
    }

    window.estimateState = estimateState;
    void renderVehicles();
    renderLineItems();
    void refreshPairedSuggestions();
    renderActiveVehicleBanner();
  }

  async function renderVehicles() {
    if (!vehiclesContainer) return;

    vehiclesContainer.innerHTML = estimateState.vehicles.map((vehicle, idx) => `
      <div
        class="vehicle-card ${estimateState.activeVehicleId === vehicle.id ? "is-active-vehicle" : ""}"
        data-vehicle-id="${vehicle.id}"
        style="
          border:1px solid ${estimateState.activeVehicleId === vehicle.id ? "rgba(76, 132, 255, .9)" : "rgba(255,255,255,.12)"};
          box-shadow:${estimateState.activeVehicleId === vehicle.id ? "0 0 0 1px rgba(76,132,255,.25) inset" : "none"};
          border-radius:14px;
          padding:14px;
          margin-top:12px;
          cursor:pointer;
        "
      >
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px;">
          <h3 style="margin:0;">Vehicle ${idx + 1}</h3>
          ${estimateState.vehicles.length > 1 && idx > 0 ? `
            <button type="button" class="ghost remove-vehicle-btn" data-vehicle-id="${vehicle.id}">
              Remove Vehicle
            </button>
          ` : ""}
        </div>

        <div class="grid3">
          <div>
            <label>Year</label>
            <select class="vehicle-year" data-vehicle-id="${vehicle.id}"></select>
          </div>

          <div>
            <label>Make</label>

            <input
              type="text"
              class="vehicle-make-search"
              data-vehicle-id="${vehicle.id}"
              placeholder="Search make..."
              autocomplete="off"
            />

            <div
              class="vehicle-make-results"
              data-vehicle-id="${vehicle.id}"
              style="display:none; margin-top:6px; border:1px solid rgba(255,255,255,.12); border-radius:12px; overflow-y:auto;"
            ></div>

            <select class="vehicle-make" data-vehicle-id="${vehicle.id}" style="display:none;">
              <option value="">Select make...</option>
            </select>
          </div>

          <div>
            <label>Model</label>
            <select class="vehicle-model" data-vehicle-id="${vehicle.id}">
              <option value="">Select model...</option>
            </select>
          </div>
        </div>
      </div>
    `).join("");

    await bindVehicleCardFields();
  }

  async function bindVehicleCardFields() {
    for (const vehicle of estimateState.vehicles) {
      const yearSelect = document.querySelector(`.vehicle-year[data-vehicle-id="${vehicle.id}"]`);
      const makeSelect = document.querySelector(`.vehicle-make[data-vehicle-id="${vehicle.id}"]`);
      const makeSearch = document.querySelector(`.vehicle-make-search[data-vehicle-id="${vehicle.id}"]`);
      const makeResults = document.querySelector(`.vehicle-make-results[data-vehicle-id="${vehicle.id}"]`);
      const modelSelect = document.querySelector(`.vehicle-model[data-vehicle-id="${vehicle.id}"]`);

      if (!yearSelect || !makeSelect || !makeSearch || !makeResults || !modelSelect) continue;

      await window.TorqueMechVehicleUI.initSharedVehicleSelector({
        yearSelect,
        makeSelect,
        makeSearch,
        makeResults,
        modelSelect,
        initialVehicle: {
          year: vehicle.year || "",
          make: vehicle.make || "",
          model: vehicle.model || "",
        },
        onChange: ({ year, make, model }) => {
          vehicle.year = year;
          vehicle.make = make;
          vehicle.model = model;
          syncEstimateMeta();
          window.estimateState = estimateState;
          void loadServices(categoryEl?.value || "");
          updateEstimateButtonState();
          renderSharedEstimateSnapshot();
          refreshQuotePreview();
        },
      });
    }

    document.querySelectorAll(".remove-vehicle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        removeVehicleCard(btn.dataset.vehicleId);
      });
    });

    document.querySelectorAll(".vehicle-card").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest("button") || e.target.closest("select") || e.target.closest("input") || e.target.closest("label")) {
          return;
        }

        setActiveVehicle(card.dataset.vehicleId);
      });
    });
  }

  // ---- Generate All Service Estimates ----
  generateAllBtn?.addEventListener("click", async () => {
    try {
      if (!lineItems.length) {
        setStatus("error", "Add at least one service first.");
        return;
      }

      for (const it of lineItems) {

        if (it.pricingMode === "flat") {
          it.estimate = calcLineItemEstimate(it);
          continue;
        }

        const req = buildLineItemEstimateRequest(it);

        const res = await apiJSON("/estimate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(req),
        });

        it.laborBreakdown = res.labor_breakdown || null;
        it.breakdownOpen = false;

        it.estimate = calcLineItemEstimate(it);
      }

      renderLineItems();
      setStatus("ok", "Quote lines updated.");
      trackClarity("estimate_generated", {
        source: "estimator",
        action: "generate_all",
        service_count: lineItems.length,
        estimate_total: lineItems.reduce((sum, it) => sum + Number(it.estimate || 0), 0)
      });
      openConfirm();

    } catch (e) {
      setStatus("error", `Generate all failed: ${e.message}`);
    }
  });

  // Populate dropdown on page load
  refreshDraftsUI();

  // ---- Init ----
  const initReady = (async () => {
    try {
      populateYears();
      await loadMakes();
      await loadCategories();
      await applyObdFromQuery();
      await renderVehicles();
      renderActiveVehicleBanner();
      syncTopVehicleToState();

      if (serviceEl) serviceEl.innerHTML = `<option value="">Select service…</option>`;
      resetServiceSearch();

      updateEstimateButtonState();
      setStatus("info", "Choose a repair job, review pricing, then add it to the quote.");
    } catch (e) {
      setStatus("error", `Init failed: ${e.message}`);
    }
  })();

  initReady.then(() => loadSharedEstimateFromPath());

  // ---- Repair Guide → Estimator preload ----
  (function preloadFromRepairGuide() {
    const params = new URLSearchParams(window.location.search);

    const year = params.get("year");
    const make = params.get("make");
    const model = params.get("model");
    const service = params.get("service");

    if (!year && !make && !model && !service) return;

    async function preloadVehicle() {
      const activeVehicle = getActiveVehicle();
      if (!activeVehicle) return;

      if (year) activeVehicle.year = year;
      if (make) activeVehicle.make = make;
      if (model) activeVehicle.model = model;

      window.estimateState = estimateState;
      await renderVehicles();
      renderActiveVehicleBanner();
      updateEstimateButtonState();
    }

    async function preloadServiceByCode(serviceCode) {
      if (!serviceCode || !categoryEl || !serviceEl) return false;

      try {
        const serviceMeta = await apiJSON(`/api/service/${encodeURIComponent(serviceCode)}`);
        const categoryKey = serviceMeta?.category;

        if (!categoryKey) {
          console.warn("No category returned for service:", serviceCode);
          return false;
        }

        categoryEl.value = categoryKey;
        await loadServices(categoryKey);

        const serviceExists = Array.from(serviceEl.options).some(
          opt => opt.value === serviceCode
        );

        if (!serviceExists) {
          console.warn("Service not found in loaded category:", serviceCode, categoryKey);
          return false;
        }

        serviceEl.value = serviceCode;
        await loadServiceMeta(serviceCode);
        serviceEl.dispatchEvent(new Event("change"));
        updateEstimateButtonState();
        return true;

      } catch (e) {
        console.warn("Service preload failed:", serviceCode, e);
        return false;
      }
    }

    initReady.then(async () => {
      try {
        await preloadVehicle();

        if (service) {
          const found = await preloadServiceByCode(service);
          if (!found) {
            console.warn("Could not auto-find service:", service);
          }
        }
      } catch (e) {
        console.warn("Repair guide preload failed:", e);
      }
    });

    document.querySelectorAll(".tm-collapse-toggle").forEach(btn=>{
      btn.addEventListener("click",()=>{
        btn.parentElement.classList.toggle("active");
      });
    });
  })();
})();
