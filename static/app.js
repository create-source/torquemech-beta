/* 🔒 LOCKED (Beta Stabilization)
   Draft load / Generate All first-click fix is working.
   Do NOT edit draft functions unless absolutely necessary.
   If changes are needed, edit app.locked.js first and diff carefully.
*/
// static/app.js — CLEAN (Beta-stable)
(() => {
  function initRepairIntelligenceDrawers() {
    if (window.__tmRepairIntelligenceDrawersBooted) return;
    window.__tmRepairIntelligenceDrawersBooted = true;

    const drawers = () => Array.from(document.querySelectorAll("[data-repair-intelligence-drawer]"));

    const closeDrawer = (drawer) => {
      if (!drawer) return;
      drawer.hidden = true;
      if (!drawers().some((item) => !item.hidden)) {
        document.body.style.overflow = "";
      }
    };

    const openDrawer = (drawer) => {
      drawers().forEach((item) => {
        if (item !== drawer) closeDrawer(item);
      });
      drawer.hidden = false;
      document.body.style.overflow = "hidden";
    };

    window.tmRepairIntelligenceOpenDrawer = (drawerId) => {
      const drawer = document.getElementById(drawerId || "");
      if (drawer) openDrawer(drawer);
    };

    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-repair-intelligence-open]");
      if (button) {
        window.tmRepairIntelligenceOpenDrawer(button.dataset.repairIntelligenceOpen || "");
        return;
      }

      const closeTarget = event.target.closest("[data-repair-intelligence-close]");
      if (closeTarget) {
        closeDrawer(closeTarget.closest("[data-repair-intelligence-drawer]"));
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      drawers().forEach(closeDrawer);
    });
  }

  initRepairIntelligenceDrawers();

  if (window.__tmEstimatorAppBooted) return;
  window.__tmEstimatorAppBooted = true;

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

  function initClearableTextFields(root = document) {
    const selector = [
      'input[type="text"]',
      'input[type="search"]',
      'input[type="tel"]',
      'input[type="email"]',
      'input[type="url"]',
      "textarea",
    ].join(",");

    const fields = Array.from(root.querySelectorAll(selector));

    fields.forEach((field) => {
      if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) return;
      if (field.readOnly || field.disabled || field.type === "hidden") return;
      if (field.closest(".tm-inline-clear-field, .tm-service-field-shell, .tm-clearable-field, .tm-repair-guide-search")) return;
      if (field.classList.contains("vehicle-model-search")) return;

      const wrapper = document.createElement("span");
      wrapper.className = "tm-clearable-field";
      if (field instanceof HTMLTextAreaElement) {
        wrapper.classList.add("tm-clearable-field--textarea");
      }

      field.parentNode?.insertBefore(wrapper, field);
      wrapper.appendChild(field);

      const clearButton = document.createElement("button");
      clearButton.type = "button";
      clearButton.className = "tm-input-clear-btn";
      clearButton.setAttribute("aria-label", `Clear ${field.getAttribute("aria-label") || field.placeholder || field.id || "field"}`);
      clearButton.hidden = true;
      clearButton.innerHTML = "&times;";
      wrapper.appendChild(clearButton);

      const update = () => {
        clearButton.hidden = !String(field.value || "").trim();
      };

      field.addEventListener("input", update);
      field.addEventListener("change", update);
      field.addEventListener("focus", update);
      clearButton.addEventListener("click", () => {
        field.value = "";
        field.dispatchEvent(new Event("input", { bubbles: true }));
        field.dispatchEvent(new Event("change", { bubbles: true }));
        update();
        field.focus({ preventScroll: true });
      });

      update();
    });
  }

  window.TorqueMechClearFields = Object.assign({}, window.TorqueMechClearFields, {
    init: initClearableTextFields,
    refresh(root = document) {
      initClearableTextFields(root);
      root.querySelectorAll(".tm-clearable-field input, .tm-clearable-field textarea").forEach((field) => {
        field.dispatchEvent(new Event("change", { bubbles: true }));
      });
    },
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initClearableTextFields(), { once: true });
  } else {
    initClearableTextFields();
  }

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
      displayModel: String(initialVehicle.displayModel || initialVehicle.model || ""),
    };

    const notifyChange = () => {
      if (typeof onChange === "function") {
        onChange({
          year: vehicle.year,
          make: vehicle.make,
          model: vehicle.model,
          displayModel: vehicle.displayModel || vehicle.model,
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
    const makeClearButton = makeSearch.parentElement?.querySelector(".vehicle-make-clear");
    const modelClearButton = modelSelect.parentElement?.querySelector(".vehicle-model-clear");

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

    const setInlineClearButton = (button, visible) => {
      if (!button) return;
      button.hidden = !visible;
      button.disabled = false;
    };

    const updateVehicleClearButtons = () => {
      setInlineClearButton(makeClearButton, Boolean((makeSearch.value || "").trim() || makeSelect.value));
      setInlineClearButton(modelClearButton, Boolean((modelSearch.value || "").trim() || modelSelect.value));
    };

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

    const MODEL_SEARCH_ALIASES = {
      LEXUS: {
        ES: ["es300", "es330", "es350"],
        GS: ["gs300", "gs350", "gs400", "gs430", "gs450h", "gs460"],
        GX: ["gx460", "gx470"],
        LX: ["lx450", "lx470", "lx570", "lx600"],
        RX: ["rx300", "rx330", "rx350", "rx400h", "rx450h", "rx500h"],
      },
    };

    const LEXUS_MODEL_DISPLAY_VARIANTS = {
      ES: ["ES300", "ES330", "ES350"],
      GS: ["GS300", "GS350", "GS400", "GS430", "GS450h", "GS460"],
      GX: ["GX460", "GX470"],
      LX: ["LX450", "LX470", "LX570", "LX600"],
      RX: ["RX300", "RX330", "RX350", "RX400h", "RX450h", "RX500h"],
    };

    const getMakeKey = () =>
      String(vehicle.make || makeSelect.value || "").trim().toUpperCase();

    const getCanonicalModelForDisplay = (displayModel) => {
      const makeKey = getMakeKey();
      if (makeKey !== "LEXUS") return displayModel;

      const normalizedDisplay = normalizeModelSearch(displayModel);
      const canonicalModel = Object.entries(LEXUS_MODEL_DISPLAY_VARIANTS).find(([, variants]) =>
        variants.some((variant) => normalizeModelSearch(variant) === normalizedDisplay)
      )?.[0];

      return canonicalModel && models.includes(canonicalModel) ? canonicalModel : displayModel;
    };

    const modelMatchesSearch = (model, query) => {
      const normalizedQuery = normalizeModelSearch(query);
      if (!normalizedQuery) return false;

      const normalizedModel = normalizeModelSearch(model);
      if (normalizedModel.includes(normalizedQuery)) return true;

      const makeKey = getMakeKey();
      const aliases = MODEL_SEARCH_ALIASES[makeKey]?.[String(model || "").trim().toUpperCase()] || [];
      return aliases.some((alias) => normalizeModelSearch(alias).includes(normalizedQuery));
    };

    const modelEqualsSearch = (option, query) => {
      const normalizedQuery = normalizeModelSearch(query);
      if (!normalizedQuery) return false;
      return (
        normalizeModelSearch(option?.display) === normalizedQuery ||
        normalizeModelSearch(option?.value) === normalizedQuery
      );
    };

    const getModelDisplayOptions = (query) => {
      const makeKey = getMakeKey();
      const seen = new Set();
      const options = [];

      models.forEach((model) => {
        const canonicalModel = String(model || "").trim();
        const variants = makeKey === "LEXUS"
          ? LEXUS_MODEL_DISPLAY_VARIANTS[canonicalModel.toUpperCase()] || []
          : [];
        const displayModels = variants.length ? variants : [canonicalModel];

        displayModels.forEach((displayModel) => {
          const displayKey = normalizeModelSearch(displayModel);
          if (!displayKey || seen.has(displayKey)) return;

          if (normalizeModelSearch(displayModel).includes(normalizeModelSearch(query)) || modelMatchesSearch(canonicalModel, query)) {
            seen.add(displayKey);
            options.push({
              value: getCanonicalModelForDisplay(displayModel),
              display: displayModel,
            });
          }
        });
      });

      return options;
    };

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
      updateVehicleClearButtons();
    };

    const renderModelResults = (query) => {
      if (modelSearch.disabled) {
        hideModelResults();
        return;
      }

      if (!normalizeModelSearch(query)) {
        hideModelResults();
        return;
      }

      const filtered = getModelDisplayOptions(query)
        .slice(0, 8);

      if (!filtered.length) {
        hideModelResults();
        return;
      }

      modelResults.innerHTML = filtered
        .map(
          ({ value, display }) => `
            <button
              type="button"
              class="model-result-item"
              data-model="${value}"
              data-model-display="${display}"
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
            >${display}</button>
          `
        )
        .join("");

      modelResults.style.display = "block";
    };

    const populateModels = async (selectedMake, selectedModel = "", selectedDisplayModel = selectedModel) => {
      models = [];
      modelSearch.value = "";
      modelSearch.disabled = true;
      updateVehicleClearButtons();
      hideModelResults();
      modelSelect.innerHTML = `<option value="">Loading models...</option>`;
      modelSelect.disabled = true;
      setModelLoading(true);

      if (!selectedMake) {
        modelSelect.innerHTML = `<option value="">Select model...</option>`;
        modelSearch.placeholder = "Select make first...";
        updateVehicleClearButtons();
        setModelLoading(false);
        return;
      }

      try {
        const selectedYear = Number(vehicle.year || yearSelect.value || 0);
        const modelUrl = selectedYear
          ? `/api/models/${encodeURIComponent(selectedMake)}?year=${selectedYear}`
          : `/api/models/${encodeURIComponent(selectedMake)}`;
        models = await vehicleUiApiJSON(modelUrl);
        modelSelect.innerHTML = `<option value="">Select model...</option>`;

        models.forEach((model) => {
          const option = document.createElement("option");
          option.value = model;
          option.textContent = model;
          modelSelect.appendChild(option);
        });

        const validSelectedModel = models.includes(selectedModel) ? selectedModel : "";
        const validSelectedDisplayModel = validSelectedModel ? selectedDisplayModel : "";
        vehicle.model = validSelectedModel;
        vehicle.displayModel = validSelectedDisplayModel || validSelectedModel;
        modelSelect.disabled = false;
        modelSelect.value = validSelectedModel;
        modelSearch.disabled = false;
        modelSearch.placeholder = "Search model...";
        modelSearch.value = validSelectedDisplayModel || validSelectedModel;
        updateVehicleClearButtons();
      } catch (_) {
        modelSelect.innerHTML = `<option value="">Select model...</option>`;
        modelSearch.placeholder = "Search model...";
        updateVehicleClearButtons();
      } finally {
        setModelLoading(false);
      }
    };

    const applyModelSelection = (selectedModel, displayModel = selectedModel) => {
      vehicle.model = selectedModel;
      vehicle.displayModel = displayModel || selectedModel;
      modelSelect.value = selectedModel;
      modelSearch.value = vehicle.displayModel;
      hideModelResults();
      updateVehicleClearButtons();
      notifyChange();
    };

    const applyMakeSelection = async (selectedMake, { focusModel = false } = {}) => {
      vehicle.make = selectedMake;
      vehicle.model = "";
      vehicle.displayModel = "";

      makeSelect.value = selectedMake;
      makeSearch.value = selectedMake;
      modelSearch.value = "";
      hideMakeResults();
      hideModelResults();
      updateVehicleClearButtons();

      notifyChange();
      await populateModels(selectedMake);

      if (focusModel && selectedMake && !modelSelect.disabled) {
        modelSelect.focus();
      }

      notifyChange();
    };

    makes = await vehicleUiApiJSON("/api/makes");
    populateMakeOptions();
    await populateModels(vehicle.make, vehicle.model, vehicle.displayModel);
    updateVehicleClearButtons();
    notifyChange();

    yearSelect.addEventListener("change", async () => {
      vehicle.year = yearSelect.value;
      vehicle.model = "";
      vehicle.displayModel = "";
      modelSelect.value = "";
      modelSearch.value = "";
      hideModelResults();
      updateVehicleClearButtons();
      notifyChange();
      await populateModels(vehicle.make);
      notifyChange();
    });

    makeSearch.addEventListener("input", () => {
      updateVehicleClearButtons();
      renderMakeResults(makeSearch.value);
    });

    makeSearch.addEventListener("focus", () => {
      if (makeSearch.value.trim()) {
        renderMakeResults(makeSearch.value);
      }
    });

    makeSearch.addEventListener("keydown", async (event) => {
      if (event.key !== "Enter") return;

      event.preventDefault();

      const typedMake = makeSearch.value.trim().toLowerCase();

      const exactMake = makes.find(
        (make) => make.toLowerCase() === typedMake
      );

      const firstVisibleMake =
        makeResults.querySelector(".make-result-item")?.dataset.make;

      const selectedMake = exactMake || firstVisibleMake;

      if (!selectedMake) return;

      await applyMakeSelection(selectedMake, { focusModel: true });
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
      const currentDisplay = vehicle.displayModel || vehicle.model;
      if (vehicle.model && normalizeModelSearch(modelSearch.value) !== normalizeModelSearch(currentDisplay)) {
        vehicle.model = "";
        vehicle.displayModel = "";
        modelSelect.value = "";
        notifyChange();
      }
      updateVehicleClearButtons();
      renderModelResults(modelSearch.value);
    });

    modelSearch.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;

      event.preventDefault();

      const modelOptions = getModelDisplayOptions(modelSearch.value);

      const exactModel = modelOptions.find(
        (option) => modelEqualsSearch(option, modelSearch.value)
      );

      if (exactModel) {
        applyModelSelection(exactModel.value, exactModel.display);
        return;
      }

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
      applyModelSelection(resultButton.dataset.model || "", resultButton.dataset.modelDisplay || resultButton.dataset.model || "");
    });

    modelSelect.addEventListener("change", () => {
      applyModelSelection(modelSelect.value);
    });

    makeClearButton?.addEventListener("click", async () => {
      vehicle.make = "";
      vehicle.model = "";
      vehicle.displayModel = "";
      makeSelect.value = "";
      makeSearch.value = "";
      modelSelect.value = "";
      modelSearch.value = "";
      hideMakeResults();
      hideModelResults();
      updateVehicleClearButtons();
      notifyChange();
      await populateModels("");
      updateVehicleClearButtons();
      notifyChange();
      makeSearch.focus({ preventScroll: true });
    });

    modelClearButton?.addEventListener("click", () => {
      vehicle.model = "";
      vehicle.displayModel = "";
      modelSelect.value = "";
      modelSearch.value = "";
      hideModelResults();
      updateVehicleClearButtons();
      notifyChange();
      modelSearch.focus({ preventScroll: true });
    });

    clearButton?.addEventListener("click", async () => {
      vehicle.year = "";
      vehicle.make = "";
      vehicle.model = "";
      vehicle.displayModel = "";

      yearSelect.value = "";
      makeSelect.value = "";
      makeSearch.value = "";
      modelSearch.value = "";
      hideMakeResults();
      hideModelResults();
      updateVehicleClearButtons();
      await populateModels("");
      updateVehicleClearButtons();
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
  const categoryClearBtn = $("categoryClearBtn");
  const serviceClearBtn = $("serviceClearBtn");
  let serviceOptions = [];
  let serviceCategories = [];
  let allServiceOptions = [];
  let allServiceOptionsVehicleKey = "";
  let serviceSearch = null;
  let serviceResults = null;
  const SERVICE_SEARCH_PLACEHOLDER = "Search services or symptoms...";
  let categorySelectionSource = "none";

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
    brake_pads: {
      label: "Brake Pads",
      category: "brakes",
      serviceCode: "front_brake_pads_replacement",
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
    diagnostic: {
      label: "Check Engine Light Diagnosis",
      category: "exhaust",
      serviceCode: "check_engine_light_diagnosis",
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
    timingChainTensioner: "timing_chain_tensioner_replacement",
    timingChainLegacy: "timing_chain_service",
  };

  const TIMING_SYSTEM_SUPPORT = {
    TOYOTA: {
      SEQUOIA: {
        type: "chain",
        show: [TIMING_SERVICE_CODES.timingChainKit, TIMING_SERVICE_CODES.timingChainTensioner],
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
    serviceSearch.placeholder = SERVICE_SEARCH_PLACEHOLDER;
    serviceSearch.disabled = false;

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

  function updateServiceClearButton() {
    if (!serviceClearBtn) return;
    const hasTypedText = Boolean((serviceSearch?.value || "").trim());
    const hasSelectedService = Boolean((serviceEl?.value || "").trim());
    const hasValue = hasTypedText || hasSelectedService;
    serviceClearBtn.hidden = !hasValue;
    serviceClearBtn.disabled = false;
  }
  updateServiceClearButton();

  function updateCategoryClearButton() {
    if (!categoryClearBtn) return;
    categoryClearBtn.hidden = !Boolean((categoryEl?.value || "").trim());
    categoryClearBtn.disabled = false;
  }
  updateCategoryClearButton();

  // Inputs
  const laborHoursEl = $("laborHours");
  const laborHoursRangeEl = $("laborHoursRange");
  const partsPriceEl = $("partsPrice");
  const laborRateEl = $("laborRate");
  const pricingModeEl = $("pricingMode");
  const flatRatePriceEl = $("flatRatePrice");
  const travelFeeEl = $("travelFee");
  const flatRateWrap = $("flatRateWrap");
  const selectedServiceContextEl = $("selectedServiceContext");
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
  const completionSuggestions = $("completionSuggestions");
  const completionSuggestionsList = $("completionSuggestionsList");
  const estimatorWorkflowShortcuts = $("estimatorWorkflowShortcuts");
  const estimatorWorkflowShortcutList = $("estimatorWorkflowShortcutList");
  const estimateTotalBar = $("estimateTotalBar");
  const estimateTotalValue = $("estimateTotalValue");
  const sharedSnapshotVehicle = $("sharedSnapshotVehicle");
  const sharedSnapshotServices = $("sharedSnapshotServices");
  const sharedSnapshotTotal = $("sharedSnapshotTotal");
  const sharedDownloadPdfBtn = $("sharedDownloadPdfBtn");
  const convertToProJobBtn = $("convertToProJobBtn");
  const convertToProJobForm = $("convertToProJobForm");
  const convertToProJobPayload = $("convertToProJobPayload");
  const findingEstimateContext = $("findingEstimateContext");
  const findingEstimateContextText = $("findingEstimateContextText");

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
  const approvalStatusEl = $("approvalStatus");
  const pdfShowGeneratedDateChk = $("pdfShowGeneratedDateChk");
  const pdfShowHourlyRateChk = $("pdfShowHourlyRateChk");
  const pdfShowLaborColumnChk = $("pdfShowLaborColumnChk");
  const pdfShowPartsColumnChk = $("pdfShowPartsColumnChk");
  const pdfShowRiskNotesChk = $("pdfShowRiskNotesChk");
  const pdfShowInspectionFindingsChk = $("pdfShowInspectionFindingsChk");
  const pdfShowLaborBreakdownChk = $("pdfShowLaborBreakdownChk");
  const quoteIdentityNudge = $("quoteIdentityNudge");

  // Customer
  const customerNameEl = $("customerName");
  const customerPhoneEl = $("customerPhone");
  const businessNameEl = $("businessName");
  const mechanicNameEl = $("mechanicName");
  const businessPhoneEl = $("businessPhone");
  const businessNoteEl = $("businessNote");

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
        displayModel: "",
        services: []
      }
    ]
  };

  window.estimateState = estimateState;

  // ---- State ----
  let lineItems = [];
  const BUSINESS_IDENTITY_SESSION_KEY = "torquemech_business_identity_v1";
  const MECHANIC_PREFERENCES_KEY = "torquemech_mechanic_preferences_v1";
  const DEFAULT_LABOR_RATE = 90;
  const DEFAULT_TRAVEL_FEE = 0;

  function getEstimatorSourceContext() {
    const params = new URLSearchParams(window.location.search);
    const source = String(params.get("source") || "").trim().toLowerCase();
    if (source !== "finding") {
      return { source: "estimator" };
    }
    return {
      source: "finding",
      customerId: String(params.get("customer_id") || "").trim(),
      customerName: String(params.get("customer_name") || "").trim(),
      vehicleId: String(params.get("vehicle_id") || "").trim(),
      findingId: String(params.get("finding_id") || "").trim(),
      problemFound: String(params.get("problem_found") || "").trim(),
      recommendedRepair: String(params.get("recommended_repair") || "").trim(),
    };
  }

  function safeReadStorage(storage, key) {
    try {
      return storage?.getItem(key) || "";
    } catch (_) {
      return "";
    }
  }

  function safeWriteStorage(storage, key, value) {
    try {
      storage?.setItem(key, value);
      return true;
    } catch (_) {
      return false;
    }
  }

  function normalizePreferenceText(value, maxLength = 180) {
    return String(value || "").trim().slice(0, maxLength);
  }

  function normalizePreferenceNumber(value, fallbackValue = 0) {
    const parsed = Number(String(value ?? "").trim());
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallbackValue;
  }

  function normalizeBusinessIdentity(identity = {}) {
    return {
      businessName: normalizePreferenceText(identity.businessName, 80),
      mechanicName: normalizePreferenceText(identity.mechanicName, 80),
      businessPhone: normalizePreferenceText(identity.businessPhone, 32),
      businessNote: normalizePreferenceText(identity.businessNote, 180),
    };
  }

  function normalizeMechanicPreferences(rawPrefs = {}) {
    const prefs = rawPrefs && typeof rawPrefs === "object" ? rawPrefs : {};
    return {
      schemaVersion: 1,
      businessIdentity: normalizeBusinessIdentity(prefs.businessIdentity || prefs),
      laborRate: normalizePreferenceNumber(prefs.laborRate, DEFAULT_LABOR_RATE),
      travelFee: normalizePreferenceNumber(prefs.travelFee, DEFAULT_TRAVEL_FEE),
      updatedAt: Number(prefs.updatedAt || Date.now()),
    };
  }

  function readMechanicPreferences() {
    const raw = safeReadStorage(window.localStorage, MECHANIC_PREFERENCES_KEY);
    if (!raw) return normalizeMechanicPreferences();

    try {
      return normalizeMechanicPreferences(JSON.parse(raw));
    } catch (_) {
      return normalizeMechanicPreferences();
    }
  }

  function writeMechanicPreferences(nextPrefs) {
    const current = readMechanicPreferences();
    const normalized = normalizeMechanicPreferences({
      ...current,
      ...(nextPrefs || {}),
      businessIdentity: {
        ...current.businessIdentity,
        ...(nextPrefs?.businessIdentity || {}),
      },
      updatedAt: Date.now(),
    });

    safeWriteStorage(window.localStorage, MECHANIC_PREFERENCES_KEY, JSON.stringify(normalized));
  }

  function getPreferredLaborRate() {
    return readMechanicPreferences().laborRate || DEFAULT_LABOR_RATE;
  }

  function getPreferredTravelFee() {
    return readMechanicPreferences().travelFee || DEFAULT_TRAVEL_FEE;
  }

  function getBusinessIdentity() {
    return normalizeBusinessIdentity({
      businessName: (businessNameEl?.value || "").trim(),
      mechanicName: (mechanicNameEl?.value || "").trim(),
      businessPhone: (businessPhoneEl?.value || "").trim(),
      businessNote: (businessNoteEl?.value || "").trim(),
    });
  }

  function applyBusinessIdentity(identity = {}) {
    const normalized = normalizeBusinessIdentity(identity);
    if (businessNameEl) businessNameEl.value = normalized.businessName;
    if (mechanicNameEl) mechanicNameEl.value = normalized.mechanicName;
    if (businessPhoneEl) businessPhoneEl.value = normalized.businessPhone;
    if (businessNoteEl) businessNoteEl.value = normalized.businessNote;
  }

  function loadBusinessIdentityFromSession() {
    try {
      const raw = safeReadStorage(window.sessionStorage, BUSINESS_IDENTITY_SESSION_KEY);
      if (!raw) return;
      applyBusinessIdentity(JSON.parse(raw) || {});
    } catch (_) {}
  }

  function persistBusinessIdentityToSession() {
    safeWriteStorage(window.sessionStorage, BUSINESS_IDENTITY_SESSION_KEY, JSON.stringify(getBusinessIdentity()));
  }

  function persistMechanicPreferencesFromControls() {
    writeMechanicPreferences({
      businessIdentity: getBusinessIdentity(),
      laborRate: pricingInputNumber(laborRateEl, getPreferredLaborRate()),
      travelFee: pricingInputNumber(travelFeeEl, getPreferredTravelFee()),
    });
    persistBusinessIdentityToSession();
  }

  function applyMechanicPreferencesToBlankControls({ identity = true, pricing = true } = {}) {
    const prefs = readMechanicPreferences();
    if (identity) {
      const hasSavedIdentity = Object.values(prefs.businessIdentity || {}).some(Boolean);
      if (hasSavedIdentity) {
        applyBusinessIdentity(prefs.businessIdentity);
        persistBusinessIdentityToSession();
      } else {
        loadBusinessIdentityFromSession();
      }
    }
    if (pricing) {
      if (laborRateEl) laborRateEl.value = String(prefs.laborRate || DEFAULT_LABOR_RATE);
      if (travelFeeEl) travelFeeEl.value = String(prefs.travelFee || DEFAULT_TRAVEL_FEE);
    }
  }

  function refreshQuoteIdentityNudge() {
    if (!quoteIdentityNudge) return;
    const hasCustomerName = !!(customerNameEl?.value || "").trim();
    const hasBusinessName = !!(businessNameEl?.value || "").trim();
    quoteIdentityNudge.classList.toggle("hidden", hasCustomerName || hasBusinessName);
  }

  applyMechanicPreferencesToBlankControls();

  const COMMONLY_ADDED_TOGETHER = [
    {
      match: ["front_brake_pads_replacement", "front brake pads replacement"],
      context: "Brake axle workflow",
      suggestions: [
        { label: "Front Rotor Inspection", serviceCode: "front_brake_rotors_replacement", stage: "Same axle", reason: "Inspect rotor thickness, scoring, and pulsation risk while the pads are apart.", weight: 100 },
        { label: "Brake Fluid Service", serviceCode: "brake_fluid_flush", stage: "Fluid check", reason: "Check fluid age, color, and pedal feel before the brake quote is closed.", weight: 66 },
        { label: "Caliper Inspection", serviceCode: "brake_diagnostic", stage: "Slide check", reason: "Confirm caliper slide, pin, and hose condition if pad wear is uneven.", weight: 58 },
      ],
    },
    {
      match: ["rear_brake_pads_replacement", "rear brake pads replacement"],
      context: "Brake axle workflow",
      suggestions: [
        { label: "Rear Rotor Inspection", serviceCode: "rear_brake_rotors_replacement", stage: "Same axle", reason: "Inspect rotor thickness, scoring, and parking-brake overlap while the pads are apart.", weight: 100 },
        { label: "Brake Fluid Service", serviceCode: "brake_fluid_flush", stage: "Fluid check", reason: "Check fluid age, color, and pedal feel before the brake quote is closed.", weight: 66 },
        { label: "Caliper Inspection", serviceCode: "brake_diagnostic", stage: "Slide check", reason: "Confirm caliper slide, pin, and hose condition if pad wear is uneven.", weight: 58 },
      ],
    },
    {
      match: ["brake pad", "brake pads"],
      context: "Brake quote workflow",
      suggestions: [
        { label: "Rotor Inspection", query: "brake rotor", stage: "Commonly checked", reason: "Mechanics often inspect rotor thickness, scoring, and pulsation risk with pad wear.", weight: 74 },
        { label: "Brake Fluid Service", serviceCode: "brake_fluid_flush", stage: "Fluid check", reason: "Check fluid age, color, and pedal feel before closing the brake quote.", weight: 60 },
        { label: "Caliper Inspection", serviceCode: "brake_diagnostic", stage: "Slide check", reason: "Useful when uneven pad wear points to slides, pins, or hose restriction.", weight: 54 },
      ],
    },
    {
      match: ["alternator_replacement", "alternator replacement", "alternator"],
      context: "Charging-system workflow",
      suggestions: [
        { label: "Battery Test", serviceCode: "battery_test", stage: "Confirm battery", reason: "Verify battery health before and after charging-system repair.", weight: 98 },
        { label: "Battery Replacement", serviceCode: "battery_replacement", stage: "If failed test", reason: "Only quote when load testing shows the battery will not hold capacity.", weight: 70 },
        { label: "Serpentine Belt Inspection", serviceCode: "serpentine_belt_replacement", stage: "Belt drive", reason: "Inspect belt condition, tension, and slip risk while the alternator path is open.", weight: 66 },
      ],
    },
    {
      match: ["starter_replacement", "starter replacement", "starter motor"],
      context: "No-start workflow",
      suggestions: [
        { label: "Battery Test", serviceCode: "battery_test", stage: "Confirm power", reason: "Separates starter failure from weak battery or voltage drop.", weight: 96 },
        { label: "Starter Circuit Inspection", serviceCode: "no_crank_diagnosis", stage: "Circuit check", reason: "Check command signal, relays, fuses, cables, and grounds before blaming the starter alone.", weight: 82 },
        { label: "Battery Cable Inspection", serviceCode: "battery_cable_replacement", stage: "Voltage drop", reason: "Cables and terminals can mimic a starter problem.", weight: 70 },
      ],
    },
    {
      match: ["water_pump_replacement", "water pump replacement", "water pump"],
      context: "Cooling-system workflow",
      suggestions: [
        { label: "Thermostat Inspection", serviceCode: "thermostat_replacement", stage: "Flow control", reason: "Commonly checked when overheating or warm-up behavior may not be pump-only.", weight: 84 },
        { label: "Coolant Service", serviceCode: "coolant_flush", stage: "Fluid condition", reason: "Check coolant age, contamination, and refill needs while the system is open.", weight: 74 },
        { label: "Radiator Hose Inspection", serviceCode: "coolant_hose_replacement_each", stage: "Leak check", reason: "Inspect swollen, soft, cracked, or leaking hoses before closing the cooling repair.", weight: 68 },
        { label: "Belt Inspection", serviceCode: "serpentine_belt_replacement", stage: "Belt drive", reason: "Check belt condition and tension when the pump is belt-driven or nearby.", weight: 58 },
      ],
    },
    {
      match: ["radiator_replacement", "radiator replacement", "radiator"],
      context: "Cooling-system workflow",
      suggestions: [
        { label: "Coolant Flush", serviceCode: "coolant_flush", stage: "Refill quality", reason: "Consider when coolant age or contamination affects the finished repair.", weight: 84 },
        { label: "Thermostat Replacement", serviceCode: "thermostat_replacement", stage: "Flow control", reason: "Check when temperature control concerns overlap with the radiator repair.", weight: 72 },
        { label: "Cooling System Pressure Test", serviceCode: "cooling_system_pressure_test", stage: "Leak check", reason: "Verify the system holds pressure before the customer handoff.", weight: 70 },
      ],
    },
    {
      match: ["wheel_bearing_replacement", "wheel bearing", "hub assembly"],
      context: "Chassis workflow",
      suggestions: [
        { label: "Suspension Inspection", serviceCode: "suspension_noise_diagnosis", stage: "Nearby check", reason: "Control arms, ball joints, and links are commonly checked while the corner is raised.", weight: 82 },
        { label: "Tire Wear Inspection", serviceCode: "tire_rotation", stage: "Road-noise check", reason: "Tire wear can imitate bearing growl and helps explain repeat noise complaints.", weight: 76 },
        { label: "Wheel Alignment", serviceCode: "wheel_alignment_4_wheel", stage: "After repair", reason: "Consider when tire wear, pull, or suspension angle concerns overlap.", weight: 64 },
      ],
    },
    {
      match: ["cooling_fan_assembly_replacement", "cooling fan", "radiator fan"],
      context: "Cooling-system workflow",
      suggestions: [
        { label: "Cooling System Pressure Test", serviceCode: "cooling_system_pressure_test", stage: "Verify system", reason: "Checks for leaks or pressure loss before the repair is closed out.", weight: 92 },
        { label: "Thermostat Replacement", serviceCode: "thermostat_replacement", stage: "Related cause", reason: "Useful when overheating behavior may not be fan-only.", weight: 56 },
      ],
    },
    {
      match: ["spark plug", "spark plugs"],
      context: "Misfire workflow",
      suggestions: [
        { label: "Ignition Coils", query: "ignition coil", stage: "Related ignition", reason: "Coils are commonly checked when plugs are part of a misfire path.", weight: 84 },
      ],
    },
    {
      match: ["thermostat"],
      context: "Cooling-system workflow",
      suggestions: [
        { label: "Coolant Service", query: "coolant flush", stage: "Fluid condition", reason: "Consider when coolant age, contamination, or refill labor affects the quote.", weight: 70 },
        { label: "Radiator Hose Inspection", serviceCode: "coolant_hose_replacement_each", stage: "Leak check", reason: "Hoses and housing seals are commonly inspected when the cooling system is open.", weight: 60 },
      ],
    },
    {
      match: ["battery"],
      context: "Starting/charging workflow",
      suggestions: [
        { label: "Charging System Inspection", query: "charging system", stage: "Confirm output", reason: "Verify alternator output and voltage stability before closing a battery quote.", weight: 88 },
        { label: "Alternator Inspection", serviceCode: "alternator_replacement", stage: "Related check", reason: "Only add when charging output or bearing/noise checks point beyond the battery.", weight: 72 },
        { label: "Battery Terminal Service", query: "battery terminal", stage: "Connection check", reason: "Corroded or loose terminals can create repeat no-start complaints.", weight: 76 },
      ],
    },
    {
      match: ["overheating", "overheating_diagnosis", "cooling system diagnosis"],
      context: "Overheating workflow",
      suggestions: [
        { label: "Cooling Pressure Test", serviceCode: "cooling_system_pressure_test", stage: "Add inspection", reason: "Pressure testing helps confirm leaks before parts are quoted.", weight: 94 },
        { label: "Thermostat Check", serviceCode: "thermostat_replacement", stage: "Related cause", reason: "Useful when temperature control or slow warm-up overlaps the complaint.", weight: 76 },
        { label: "Radiator Fan Inspection", query: "radiator fan", stage: "Airflow check", reason: "Fan command and airflow checks are common on idle or A/C overheating complaints.", weight: 72 },
      ],
    },
    {
      match: ["serpentine belt", "drive belt"],
      context: "Belt-drive workflow",
      suggestions: [
        { label: "Belt Tensioner", query: "belt tensioner", stage: "Same access", reason: "Tensioner wear can shorten belt life or cause noise.", weight: 86 },
        { label: "Idler Pulley", query: "idler pulley", stage: "Same access", reason: "Pulley noise or bearing play is often checked with the belt off.", weight: 78 },
      ],
    },
  ];

  const QUOTE_COMPLETION_CHECKS = [
    {
      match: ["front_brake_pads_replacement", "rear_brake_pads_replacement", "brake pad", "brake pads"],
      reminders: [
        { label: "Brake Rotors", query: "brake rotor", stage: "Same visit", reason: "Confirm rotor condition before final approval.", weight: 80 },
        { label: "Brake Fluid Flush", serviceCode: "brake_fluid_flush", stage: "System check", reason: "Consider if fluid condition affects the customer handoff.", weight: 60 },
      ],
    },
    {
      match: ["cooling_fan_assembly_replacement", "radiator fan", "water_pump_replacement", "thermostat_replacement"],
      reminders: [
        { label: "Cooling System Pressure Test", serviceCode: "cooling_system_pressure_test", stage: "Verify system", reason: "Helps catch leaks or pressure loss before the quote is finalized.", weight: 88 },
        { label: "Radiator Hose Inspection", serviceCode: "coolant_hose_replacement_each", stage: "Leak check", reason: "Quickly surfaces hose condition while the cooling path is already in view.", weight: 66 },
        { label: "Thermostat Inspection", serviceCode: "thermostat_replacement", stage: "Related cause", reason: "Consider only if temperature behavior points beyond the quoted repair.", weight: 52 },
      ],
    },
    {
      match: ["starter_replacement", "starter", "battery_replacement", "battery"],
      reminders: [
        { label: "Battery Test", serviceCode: "battery_test", stage: "Confirm power", reason: "Keeps no-start quotes from missing low-voltage causes.", weight: 90 },
        { label: "Starter Circuit Inspection", serviceCode: "no_crank_diagnosis", stage: "Circuit check", reason: "Good check when command signal, relay, fuse, or ground questions remain.", weight: 72 },
        { label: "Battery Cable Inspection", serviceCode: "battery_cable_replacement", stage: "Voltage drop", reason: "Good check when cable corrosion or looseness is present.", weight: 64 },
      ],
    },
    {
      match: ["wheel_bearing_replacement", "wheel bearing", "sway_bar_link_replacement", "suspension"],
      reminders: [
        { label: "Suspension Inspection", serviceCode: "suspension_noise_diagnosis", stage: "Nearby check", reason: "Useful when the corner is already raised and noise source is still being confirmed.", weight: 78 },
        { label: "Tire Wear Inspection", serviceCode: "tire_rotation", stage: "Road-noise check", reason: "Consider when tire chop or cupping may be part of the noise complaint.", weight: 70 },
      ],
    },
  ];

  function getCustomerOutputOptions() {
    return {
      showGeneratedDate: pdfShowGeneratedDateChk ? !!pdfShowGeneratedDateChk.checked : true,
      showHourlyRate: pdfShowHourlyRateChk ? !!pdfShowHourlyRateChk.checked : false,
      showLaborColumn: pdfShowLaborColumnChk ? !!pdfShowLaborColumnChk.checked : false,
      showPartsColumn: pdfShowPartsColumnChk ? !!pdfShowPartsColumnChk.checked : false,
      showRiskNotes: pdfShowRiskNotesChk ? !!pdfShowRiskNotesChk.checked : true,
      showInspectionFindings: pdfShowInspectionFindingsChk ? !!pdfShowInspectionFindingsChk.checked : true,
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
          vehicleDisplayModel: it.vehicleDisplayModel || getVehicleDisplayModel(vehicle),
          serviceCode: it.serviceCode,
          title: it.serviceText,
          laborHours: it.laborHours,
          laborRate: it.laborRate,
          partsTotal: it.partsPrice,
          lineTotal: it.estimate,
          status: normalizeRepairStatus(it.status),
          notes: it.notes || "",
          inspectionFindings: it.inspectionFindings || ""
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

  function getVehicleDisplayModel(vehicle) {
    return String(vehicle?.displayModel || vehicle?.model || "").trim();
  }

  function getVehicleDetails(vehicle) {
    if (!vehicle) return "";
    return [vehicle.year, vehicle.make, getVehicleDisplayModel(vehicle)].filter(Boolean).join(" ");
  }

  function normalizeVehicleLabelKey(value) {
    return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  function getKnownSparkPlugEngineLabel(vehicle) {
    const make = normalizeVehicleLabelKey(vehicle?.make);
    const canonicalModel = normalizeVehicleLabelKey(vehicle?.model);
    const displayModel = normalizeVehicleLabelKey(getVehicleDisplayModel(vehicle));

    if (make !== "LEXUS") return "";

    const knownV8Models = new Set(["GX460", "GX470", "LX450", "LX470", "LX570"]);
    if (knownV8Models.has(displayModel) || knownV8Models.has(canonicalModel)) {
      return "V8";
    }

    return "";
  }

  function cleanCustomerServiceLabel(serviceCode, serviceText, vehicle) {
    const code = String(serviceCode || "").trim();
    const label = String(serviceText || code || "Service").trim();
    const isSparkPlugService =
      code === "spark_plug_replacement_4_cyl" ||
      code === "spark_plug_replacement_v6_v8" ||
      /^spark\s+plug\s+replacement\b/i.test(label);

    if (!isSparkPlugService) {
      return label;
    }

    const engineLabel = getKnownSparkPlugEngineLabel(vehicle);
    return engineLabel ? `Spark Plug Replacement (${engineLabel})` : "Spark Plug Replacement";
  }

  function cleanGenericFitmentLabel(serviceText) {
    return String(serviceText || "Service")
      .replace(/\s*\((?:diesel|diesel,\s*if applicable|4x4,\s*if applicable|gdi\/port|manual|if applicable|if supported)\)\s*/gi, " ")
      .replace(/\s{2,}/g, " ")
      .trim();
  }

  function cleanCustomerFacingServiceLabel(serviceCode, serviceText, vehicle) {
    return cleanGenericFitmentLabel(cleanCustomerServiceLabel(serviceCode, serviceText, vehicle));
  }

  const REPAIR_BLUEPRINT_BY_SERVICE = [
    { match: ["alternator_replacement"], slug: "alternator-replacement" },
    { match: ["front_brake_pads_replacement", "rear_brake_pads_replacement", "brake pad"], slug: "brake-pad-replacement" },
    { match: ["water_pump_replacement"], slug: "water-pump-replacement" },
    { match: ["spark_plug_replacement_4_cyl", "spark_plug_replacement_v6_v8", "spark plug"], slug: "spark-plug-replacement" },
    { match: ["radiator_replacement"], slug: "radiator-replacement" },
    { match: ["starter_replacement"], slug: "starter-replacement" },
    { match: ["wheel_bearing_replacement_front", "wheel_bearing_replacement_rear", "wheel bearing"], slug: "wheel-bearing-replacement" },
    { match: ["thermostat_replacement"], slug: "thermostat-replacement" },
  ];

  function getRepairBlueprintSlug(serviceCode, serviceText = "") {
    const source = `${serviceCode || ""} ${serviceText || ""}`.toLowerCase().replace(/[_-]+/g, " ");
    const code = String(serviceCode || "").toLowerCase();
    const matched = REPAIR_BLUEPRINT_BY_SERVICE.find((entry) =>
      entry.match.some((term) => {
        const normalized = String(term || "").toLowerCase();
        return code.includes(normalized) || source.includes(normalized.replace(/[_-]+/g, " "));
      })
    );
    return matched?.slug || "";
  }

  function getCurrentVehicleSnapshot() {
    const vehicle = getActiveVehicle() || estimateState.vehicles[0] || null;
    return {
      year: vehicle?.year || "",
      make: vehicle?.make || "",
      model: vehicle?.model || "",
      displayModel: getVehicleDisplayModel(vehicle),
    };
  }

  function buildRepairBlueprintHref(serviceCode, serviceText = "") {
    const slug = getRepairBlueprintSlug(serviceCode, serviceText);
    if (!slug) return "";

    const vehicle = getCurrentVehicleSnapshot();
    const params = new URLSearchParams();
    if (vehicle.year) params.set("year", vehicle.year);
    if (vehicle.make) params.set("make", vehicle.make);
    if (vehicle.model) params.set("model", vehicle.model);
    if (vehicle.displayModel) params.set("displayModel", vehicle.displayModel);
    if (serviceCode) params.set("service", serviceCode);
    params.set("source", "estimator");

    const query = params.toString();
    return `/repair-guides/${slug}${query ? `?${query}` : ""}`;
  }

  const SYSTEM_HUB_BY_SERVICE = [
    { match: ["brake", "rotor", "caliper"], slug: "brake-system-repairs" },
    { match: ["thermostat", "radiator", "cooling", "coolant", "water pump", "fan"], slug: "cooling-system-diagnostics" },
    { match: ["battery", "alternator", "starter", "charging", "no crank", "serpentine"], slug: "charging-starting-system" },
    { match: ["spark plug", "ignition", "misfire", "maf", "oxygen sensor", "fuel trim"], slug: "engine-performance-misfire-diagnostics" },
    { match: ["evap", "purge", "vent valve", "catalytic", "emission", "smoke test"], slug: "emissions-evap-diagnostics" },
  ];

  function buildSystemHubHref(serviceCode = "", serviceText = "") {
    const source = normalizeServiceSearch(`${serviceCode || ""} ${serviceText || ""}`);
    const matched = SYSTEM_HUB_BY_SERVICE.find((entry) =>
      entry.match.some((term) => source.includes(normalizeServiceSearch(term)))
    );
    if (!matched) return "";

    const vehicle = getCurrentVehicleSnapshot();
    const params = new URLSearchParams();
    if (vehicle.year) params.set("year", vehicle.year);
    if (vehicle.make) params.set("make", vehicle.make);
    if (vehicle.model) params.set("model", vehicle.model);
    if (vehicle.displayModel) params.set("displayModel", vehicle.displayModel);
    if (serviceCode) params.set("service", serviceCode);
    params.set("source", "estimator");

    const query = params.toString();
    return `/repair-systems/${matched.slug}${query ? `?${query}` : ""}`;
  }

  function getVehicleLabel(vehicle, idxOverride = null) {
    if (!vehicle) return "No vehicle selected";

    const idx = idxOverride ?? estimateState.vehicles.findIndex(v => v.id === vehicle.id);
    const title = `Vehicle ${idx + 1}`;
    const details = getVehicleDetails(vehicle);

    return details ? `${title} — ${details}` : title;
  }

  function getCustomerVehicleLabel(vehicleOrLabel) {
    if (typeof vehicleOrLabel === "string") {
      const label = vehicleOrLabel.trim();
      return label.replace(/^Vehicle\s+\d+\s+[—-]\s+/i, "").replace(/^Vehicle\s+\d+\s*$/i, "Vehicle").trim();
    }

    const details = [
      vehicleOrLabel?.year,
      vehicleOrLabel?.make,
      getVehicleDisplayModel(vehicleOrLabel),
    ].filter(Boolean).join(" ");
    return details || "Vehicle";
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
  let isAddingLineItem = false;
  let isGeneratingAllLines = false;

  function createLineItemId() {
    if (window.crypto?.randomUUID) {
      return `line_${window.crypto.randomUUID()}`;
    }
    return `line_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  }

  function getLineItemById(lineItemId) {
    return lineItems.find((it) => it.id === lineItemId) || null;
  }

  function ensureUniqueLineItemIds(items) {
    const seen = new Set();
    return (Array.isArray(items) ? items : []).map((item) => {
      const next = item && typeof item === "object" ? item : {};
      let id = String(next.id || "").trim();
      if (!id || seen.has(id)) id = createLineItemId();
      seen.add(id);
      return { ...next, id };
    });
  }

  function hasOpenLineEdit() {
    return !!(activeEditingLineId && getLineItemById(activeEditingLineId));
  }

  function focusOpenLineEdit() {
    document.querySelector(".pricing-controls")?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }

  // ---- Saved Drafts (localStorage) ----
  const DRAFTS_KEY = "torquemech_drafts_v1";
  const LAST_DRAFT_ID_KEY = "torquemech_last_draft_id_v1";
  const DRAFT_SCHEMA_VERSION = 2;
  const MAX_DRAFTS = 25;
  let activeDraftId = "";

  function getDrafts() {
    try {
      const raw = JSON.parse(localStorage.getItem(DRAFTS_KEY) || "[]");
      const drafts = (Array.isArray(raw) ? raw : [])
        .map(normalizeDraft)
        .filter(Boolean);
      if (drafts.length !== (Array.isArray(raw) ? raw.length : 0)) {
        setDrafts(drafts);
      }
      return drafts;
    } catch {
      try {
        localStorage.removeItem(DRAFTS_KEY);
        localStorage.removeItem(LAST_DRAFT_ID_KEY);
      } catch (_) {}
      return [];
    }
  }

  function setDrafts(arr) {
    const normalized = (Array.isArray(arr) ? arr : [])
      .map(normalizeDraft)
      .filter(Boolean)
      .slice(0, MAX_DRAFTS);
    localStorage.setItem(DRAFTS_KEY, JSON.stringify(normalized));
  }

  function draftLabel(d) {
    return `${d.title} - ${new Date(d.savedAt).toLocaleString()}`;
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
    if (customerQuoteFinalActions) {
      customerQuoteFinalActions.hidden = true;
    }
    if (estimateSavedLinkText) {
      estimateSavedLinkText.textContent = lastSavedEstimateLink;
    }
  }

  function hideEstimateSavedBlock() {
    lastSavedEstimateLink = "";
    if (estimateSavedBlock) estimateSavedBlock.hidden = true;
    if (estimateSavedLinkText) estimateSavedLinkText.textContent = "";
    if (customerQuoteFinalActions) customerQuoteFinalActions.hidden = false;
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
    draftsSelect.disabled = drafts.length === 0;
    if (loadDraftBtn) loadDraftBtn.disabled = drafts.length === 0;
    if (deleteDraftBtn) deleteDraftBtn.disabled = drafts.length === 0;
    if (drafts.length) {
      let lastDraftId = activeDraftId;
      try {
        lastDraftId = lastDraftId || localStorage.getItem(LAST_DRAFT_ID_KEY) || "";
      } catch (_) {}
      if (lastDraftId && drafts.some((d) => d.id === lastDraftId)) {
        draftsSelect.value = lastDraftId;
      }
    }

    if (draftsMsg) {
      draftsMsg.textContent = drafts.length
        ? `${drafts.length} saved on this device.`
        : "No saved estimates yet.";
    }
  }

  function buildDraftTitle() {
    const currentVehicle = getCurrentVehicleSnapshot();
    const vehicle = [currentVehicle.year, currentVehicle.make, currentVehicle.displayModel || currentVehicle.model].filter(Boolean).join(" ");
    const servicesCount = lineItems.length;
    return (vehicle || "Estimate") + (servicesCount ? ` (${servicesCount} service${servicesCount > 1 ? "s" : ""})` : "");
  }

  function serializeDraft(existingDraft = null) {
    const currentVehicle = getCurrentVehicleSnapshot();
    const now = Date.now();
    return {
      schemaVersion: DRAFT_SCHEMA_VERSION,
      id: existingDraft?.id || activeDraftId || String(now),
      shareId: existingDraft?.shareId || createShareId(),
      createdAt: Number(existingDraft?.createdAt || existingDraft?.savedAt || now),
      savedAt: now,
      updatedAt: now,
      title: buildDraftTitle(),

      vehicle: {
        year: currentVehicle.year,
        make: currentVehicle.make,
        model: currentVehicle.model,
        displayModel: currentVehicle.displayModel || currentVehicle.model,
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
      businessIdentity: getBusinessIdentity(),

      lineItems: ensureUniqueLineItemIds(lineItems).map((it) =>
        normalizeDraftLineItem(it, currentVehicle)
      ),
    };
  }

  function normalizeDraft(d) {
    if (!d || typeof d !== "object") return null;

    const now = Date.now();
    const vehicle = {
      id: "veh_1",
      year: String(d.vehicle?.year || d.year || "").trim(),
      make: String(d.vehicle?.make || d.make || "").trim(),
      model: String(d.vehicle?.model || d.model || "").trim(),
      displayModel: String(d.vehicle?.displayModel || d.displayModel || d.vehicle?.model || d.model || "").trim(),
    };
    const customer = d.customer || {};
    const businessIdentity = d.businessIdentity || {};
    const savedAt = Number(d.savedAt || d.updatedAt || now);
    const lineItemSource = Array.isArray(d.lineItems) ? d.lineItems : [];
    const normalizedLineItems = ensureUniqueLineItemIds(
      lineItemSource.map((it) => normalizeDraftLineItem(it, vehicle))
    );
    const fallbackTitle = [
      getVehicleDetails(vehicle) || "Estimate",
      normalizedLineItems.length
        ? `(${normalizedLineItems.length} service${normalizedLineItems.length === 1 ? "" : "s"})`
        : "",
    ].filter(Boolean).join(" ");

    return {
      schemaVersion: DRAFT_SCHEMA_VERSION,
      id: String(d.id || savedAt || now),
      shareId: String(d.shareId || createShareId()),
      createdAt: Number(d.createdAt || savedAt || now),
      savedAt,
      updatedAt: Number(d.updatedAt || savedAt || now),
      title: String(d.title || fallbackTitle).trim() || "Saved estimate",
      vehicle,
      customer: {
        agrees: customer.agrees !== false,
        name: String(customer.name || "").trim(),
        phone: String(customer.phone || "").trim(),
        notes: String(customer.notes || "").trim(),
      },
      businessIdentity: {
        businessName: String(businessIdentity.businessName || "").trim(),
        mechanicName: String(businessIdentity.mechanicName || "").trim(),
        businessPhone: String(businessIdentity.businessPhone || "").trim(),
        businessNote: String(businessIdentity.businessNote || "").trim(),
      },
      signatureDataUrl: null,
      lineItems: normalizedLineItems,
    };
  }

  function normalizeDraftLineItem(it, fallbackVehicle) {
    const pricingMode = (it?.pricingMode || "hourly").trim() === "flat" ? "flat" : "hourly";
    const vehicleId = it?.vehicleId || fallbackVehicle?.id || "veh_1";
    const vehicleYear = it?.vehicleYear || fallbackVehicle?.year || "";
    const vehicleMake = it?.vehicleMake || fallbackVehicle?.make || "";
    const vehicleModel = it?.vehicleModel || fallbackVehicle?.model || "";
    const vehicleDisplayModel = it?.vehicleDisplayModel || fallbackVehicle?.displayModel || vehicleModel;
    const vehicleLabel = it?.vehicleLabel || getVehicleLabel({ id: vehicleId, year: vehicleYear, make: vehicleMake, model: vehicleModel, displayModel: vehicleDisplayModel }, 0);
    const flatRatePrice = normalizeMoneyValue(it?.flatRatePrice);
    const travelFee = normalizeMoneyValue(it?.travelFee);
    const laborHours = normalizeMoneyValue(it?.laborHours);
    const partsPrice = normalizeMoneyValue(it?.partsPrice);
    const laborRate = normalizeMoneyValue(it?.laborRate);
    const normalizedEstimate = Number(it?.estimate);

    const normalized = {
      ...it,
      id: it?.id || createLineItemId(),
      vehicleId,
      vehicleLabel,
      vehicleYear,
      vehicleMake,
      vehicleModel,
      vehicleDisplayModel,
      serviceCode: String(it?.serviceCode || "").trim(),
      serviceText: cleanCustomerFacingServiceLabel(
        it?.serviceCode,
        it?.serviceText || it?.serviceCode || "Service",
        { id: vehicleId, year: vehicleYear, make: vehicleMake, model: vehicleModel, displayModel: vehicleDisplayModel }
      ),
      pricingMode,
      flatRatePrice,
      travelFee,
      laborHours,
      partsPrice,
      laborRate,
      status: normalizeRepairStatus(it?.status),
      notes: (it?.notes || "").trim() || null,
      inspectionFindings: (it?.inspectionFindings || "").trim(),
      estimate: Number.isFinite(normalizedEstimate) ? normalizedEstimate : null,
      laborBreakdown: it?.laborBreakdown || null,
      breakdownOpen: false,
    };
    if (normalized.estimate == null) {
      normalized.estimate = calcLineItemEstimate(normalized);
    }
    return normalized;
  }

    async function applyDraft(d) {
      const hasDraftBusinessIdentity = Object.prototype.hasOwnProperty.call(d || {}, "businessIdentity");
      d = normalizeDraft(d);
      if (!d) return;

      try {
        closeConfirm();
      } catch (_) {}
      activeDraftId = d.id;
      try {
        localStorage.setItem(LAST_DRAFT_ID_KEY, activeDraftId);
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
            displayModel: d.vehicle?.displayModel || d.vehicle?.model || "",
            services: []
          }
        ]
      };

      window.estimateState = estimateState;

      // Legacy draft support:
      // if old line items do not have vehicleId / vehicleLabel, assign them to Vehicle 1
      lineItems = ensureUniqueLineItemIds(
        (Array.isArray(d.lineItems) ? d.lineItems : []).map((it) =>
          normalizeDraftLineItem(it, estimateState.vehicles[0])
        )
      );
      estimateState.activeVehicleId = "veh_1";

      // Sync top-level customer fields
      if (customerAgreesChk) customerAgreesChk.checked = !!d.customer?.agrees;
      if (customerNameEl) customerNameEl.value = d.customer?.name || "";
      if (customerPhoneEl) customerPhoneEl.value = d.customer?.phone || "";
      if (notesEl) notesEl.value = d.customer?.notes || "";
      if (hasDraftBusinessIdentity) {
        applyBusinessIdentity(d.businessIdentity);
        persistBusinessIdentityToSession();
      } else {
        applyMechanicPreferencesToBlankControls({ identity: true, pricing: false });
      }
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
      showEstimateSavedBlock(d);

      if (draftsMsg) draftsMsg.textContent = `Loaded from this device: ${d.title}`;
    }

  function saveCurrentDraft(options = {}) {
    const quiet = !!options.quiet;
    if (hasOpenLineEdit()) {
      if (!quiet && draftsMsg) draftsMsg.textContent = "Save the current line edit before saving this estimate.";
      setStatus("error", "Save the current line edit before saving this estimate.");
      focusOpenLineEdit();
      return null;
    }

    if (!lineItems.length) {
      if (!quiet && draftsMsg) draftsMsg.textContent = "Add at least one quoted service before saving.";
      setStatus("error", "Add at least one quoted service before saving.");
      return null;
    }

    const drafts = getDrafts();
    const existing = activeDraftId
      ? drafts.find((x) => x.id === activeDraftId)
      : null;
    const d = normalizeDraft(serializeDraft(existing));
    if (!d) return null;

    const nextDrafts = [
      d,
      ...drafts.filter((x) => x.id !== d.id && x.shareId !== d.shareId),
    ];
    if (nextDrafts.length > MAX_DRAFTS) nextDrafts.length = MAX_DRAFTS;

    try {
      setDrafts(nextDrafts);
    } catch (e) {
      if (!quiet && draftsMsg) draftsMsg.textContent = "Unable to save this estimate on this device. Download the PDF to keep a copy.";
      setStatus("error", "Unable to save this estimate on this device.");
      return null;
    }
    activeDraftId = d.id;
    try {
      localStorage.setItem(LAST_DRAFT_ID_KEY, activeDraftId);
    } catch (_) {}
    refreshDraftsUI();
    if (draftsSelect) draftsSelect.value = d.id;
    showEstimateSavedBlock(d);

    if (draftsMsg) {
      draftsMsg.textContent = quiet
        ? `Quote ready: ${d.title}`
        : `Saved: ${d.title}`;
    }
    return d;
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
    if (activeDraftId === id) {
      activeDraftId = "";
      hideEstimateSavedBlock();
      try {
        localStorage.removeItem(LAST_DRAFT_ID_KEY);
      } catch (_) {}
    }
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
  installPricingNumberFocusBehavior(laborRateEl, String(getPreferredLaborRate()));
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

  const REPAIR_STATUS_OPTIONS = [
    { value: "recommended", label: "Recommended" },
    { value: "diagnosed", label: "Diagnosed" },
    { value: "urgent", label: "Urgent" },
    { value: "monitor", label: "Monitor" },
  ];

  function normalizeRepairStatus(value) {
    const status = String(value || "").trim().toLowerCase();
    return REPAIR_STATUS_OPTIONS.some((option) => option.value === status)
      ? status
      : "recommended";
  }

  function getRepairStatusLabel(value) {
    const status = normalizeRepairStatus(value);
    return REPAIR_STATUS_OPTIONS.find((option) => option.value === status)?.label || "Recommended";
  }

  function renderRepairStatusOptions(selectedValue) {
    const selected = normalizeRepairStatus(selectedValue);
    return REPAIR_STATUS_OPTIONS
      .map((option) => `<option value="${option.value}"${option.value === selected ? " selected" : ""}>${option.label}</option>`)
      .join("");
  }

  function normalizeMoneyValue(value, fallbackValue = 0) {
    const parsed = Number(String(value ?? "").trim());
    if (!Number.isFinite(parsed) || parsed < 0) return fallbackValue;
    return parsed;
  }

  function pricingInputNumber(inputEl, fallbackValue = 0) {
    return normalizeMoneyValue(inputEl?.value, fallbackValue);
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

  function lineItemEstimateValue(it) {
    const value = Number(it?.estimate);
    return Number.isFinite(value) ? value : 0;
  }

  function quoteTotal() {
    return lineItems.reduce((sum, it) => sum + lineItemEstimateValue(it), 0);
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
    const vehicle = [currentVehicle.year, currentVehicle.make, currentVehicle.displayModel || currentVehicle.model].filter(Boolean).join(" ");

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
    const vehicle = [currentVehicle.year, currentVehicle.make, currentVehicle.displayModel || currentVehicle.model].filter(Boolean).join(" ");
    const total = quoteTotal();

    const lines = [];

    if (customerName) {
      lines.push(`Hi ${customerName},`);
      lines.push("");
    } else {
      lines.push("Hi,");
      lines.push("");
    }

    lines.push("Here is the repair quote prepared for customer review:");
    lines.push("");

    if (vehicle) {
      lines.push(`Vehicle: ${vehicle}`);
      lines.push("");
    }

    lineItems.forEach((it) => {
      const statusLabel = getRepairStatusLabel(it.status);
      const travelNote = Number(it.travelFee || 0) > 0 ? `Includes ${money(it.travelFee)} travel` : "";
      lines.push(`- ${it.serviceText || "Repair service"}`);
      lines.push(`  Status: ${statusLabel}`);
      lines.push(`  Estimate: ${money(it.estimate)}`);
      if (it.pricingMode === "flat") lines.push("  Pricing: Flat-rate");
      if (travelNote) lines.push(`  ${travelNote}`);
      lines.push(`  Estimate note: ${getEstimateRiskNote(it)}`);
      lines.push("");
    });

    lines.push(`Quote Total: ${money(total)}`);

    const notes = (notesEl?.value || "").trim();
    if (notes) {
      lines.push("");
      lines.push(`Notes: ${notes}`);
    }

    lines.push("");
    lines.push("Final pricing may vary after inspection, taxes, parts confirmation, or additional repair needs.");

    return lines.join("\n");
  }

  function buildEstimateEmailSubject() {
    const currentVehicle = getCurrentVehicleSnapshot();
    const vehicle = [currentVehicle.year, currentVehicle.make, currentVehicle.displayModel || currentVehicle.model].filter(Boolean).join(" ");
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
    const parts = normalizeMoneyValue(it.partsPrice);
    const travel = normalizeMoneyValue(it.travelFee);

    let base = 0;

    if (pricingMode === "flat") {
      base = normalizeMoneyValue(it.flatRatePrice) + parts;
    } else {
      const laborHours = normalizeMoneyValue(it.laborHours);
      const laborRate = normalizeMoneyValue(it.laborRate);
      base = (laborHours * laborRate) + parts;
    }

    return Math.round(base + travel);
  }

  function getLineItemCostBreakdown(it = {}) {
    const pricingMode = (it.pricingMode || "hourly").trim() === "flat" ? "flat" : "hourly";
    const laborHours = normalizeMoneyValue(it.laborHours);
    const laborRate = normalizeMoneyValue(it.laborRate);
    const flatRatePrice = normalizeMoneyValue(it.flatRatePrice);
    const partsPrice = normalizeMoneyValue(it.partsPrice);
    const travelFee = normalizeMoneyValue(it.travelFee);
    const laborTotal = pricingMode === "flat" ? flatRatePrice : laborHours * laborRate;
    const total = Math.round(laborTotal + partsPrice + travelFee);

    return {
      pricingMode,
      laborHours,
      laborRate,
      flatRatePrice,
      laborTotal,
      partsPrice,
      hasParts: partsPrice > 0,
      travelFee,
      hasTravel: travelFee > 0,
      total,
    };
  }

  function buildProJobConversionPayload() {
    const vehicle = getCurrentVehicleSnapshot();
    const sourceContext = getEstimatorSourceContext();
    return {
      source: sourceContext.source || "estimator",
      createdAt: new Date().toISOString(),
      vehicle,
      customerId: sourceContext.customerId || "",
      vehicleId: sourceContext.vehicleId || "",
      findingId: sourceContext.findingId || "",
      sourceContext,
      notes: (notesEl?.value || "").trim(),
      customer: {
        name: (customerNameEl?.value || sourceContext.customerName || "").trim(),
        phone: (customerPhoneEl?.value || "").trim(),
      },
      lineItems: ensureUniqueLineItemIds(lineItems).map((it) => {
        const cost = getLineItemCostBreakdown(it);
        return {
          id: it.id || createLineItemId(),
          serviceCode: String(it.serviceCode || "").trim(),
          serviceText: String(it.serviceText || it.serviceCode || "Service").trim(),
          pricingMode: cost.pricingMode,
          laborHours: cost.laborHours,
          laborRate: cost.laborRate,
          laborTotal: cost.laborTotal,
          partsTotal: cost.partsPrice,
          travelFee: cost.travelFee,
          grandTotal: cost.total,
          notes: (it.notes || "").trim(),
          description: (it.inspectionFindings || "").trim(),
        };
      }),
    };
  }

  function getVisibleLineItemPricingMeta(it = {}, outputOptions = getCustomerOutputOptions()) {
    const cost = getLineItemCostBreakdown(it);
    const pricingMeta = [];

    if (!outputOptions.showLaborColumn && !outputOptions.showPartsColumn) {
      return pricingMeta;
    }

    if (outputOptions.showLaborColumn) {
      pricingMeta.push(cost.pricingMode === "flat"
        ? { label: "Job", value: money(cost.laborTotal), kind: "labor" }
        : { label: "Labor", value: `${cost.laborHours.toFixed(1)}h`, kind: "labor" });
    }

    if (outputOptions.showPartsColumn) {
      pricingMeta.push({
        label: "Parts",
        value: cost.hasParts ? money(cost.partsPrice) : "None",
        kind: "parts",
        empty: !cost.hasParts,
      });
    }

    if (outputOptions.showHourlyRate && it.pricingMode !== "flat") {
      pricingMeta.push({ label: "Rate", value: `$${Math.round(cost.laborRate).toLocaleString()}/hr`, kind: "rate" });
    }

    if (cost.hasTravel) {
      pricingMeta.push({ label: "Travel", value: money(cost.travelFee), kind: "travel" });
    }

    return pricingMeta;
  }

  function renderCostBreakdownHtml(it = {}, outputOptions = getCustomerOutputOptions()) {
    const showLabor = !!outputOptions.showLaborColumn;
    const showParts = !!outputOptions.showPartsColumn;

    if (!showLabor && !showParts) {
      return "";
    }

    const cost = getLineItemCostBreakdown(it);
    const rows = [];

    if (showLabor) {
      const laborLabel = cost.pricingMode === "flat" ? "Job labor" : "Labor";
      const laborDetail = cost.pricingMode === "flat"
        ? "Flat job price"
        : `${cost.laborHours.toFixed(1)}h @ $${Math.round(cost.laborRate).toLocaleString()}/hr`;

      rows.push(`
        <div class="tm-cost-breakdown__row tm-cost-breakdown__row--labor">
          <span><strong>${laborLabel}</strong><em>${laborDetail}</em></span>
          <b>${money(cost.laborTotal)}</b>
        </div>
      `);
    }

    if (showParts) {
      const partsDetail = cost.hasParts ? "Parts subtotal" : "No parts added";
      rows.push(`
        <div class="tm-cost-breakdown__row tm-cost-breakdown__row--parts${cost.hasParts ? "" : " is-empty"}">
          <span><strong>Parts</strong><em>${partsDetail}</em></span>
          <b>${money(cost.partsPrice)}</b>
        </div>
      `);
    }

    const travelDetail = cost.hasTravel ? "Mobile/travel fee" : "No travel fee";
    if (cost.hasTravel) {
      rows.push(`
        <div class="tm-cost-breakdown__row tm-cost-breakdown__row--travel">
          <span><strong>Travel</strong><em>${travelDetail}</em></span>
          <b>${money(cost.travelFee)}</b>
        </div>
      `);
    }

    return `
      <div class="tm-cost-breakdown" aria-label="Line item cost breakdown">
        ${rows.join("")}
        <div class="tm-cost-breakdown__row tm-cost-breakdown__row--total">
          <span><strong>Line total</strong><em>Prepared estimate subtotal</em></span>
          <b>${money(cost.total)}</b>
        </div>
      </div>
    `;
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

  function getSelectedServiceName() {
    if (!serviceEl?.value) return "";
    return serviceEl.options[serviceEl.selectedIndex]?.textContent?.trim() || serviceEl.value;
  }

  function getSelectedServiceDisplayName() {
    const serviceName = getSelectedServiceName();
    if (!serviceName) return "";
    return cleanCustomerFacingServiceLabel(serviceEl?.value, serviceName, getActiveVehicle());
  }

  function getSelectedServiceCategoryName() {
    const categoryKey = serviceMeta?.category || categoryEl?.value || "";
    return getServiceCategoryName(categoryKey) || categoryKey || "Service";
  }

  function renderSelectedServiceContext() {
    if (!selectedServiceContextEl) return;

    const serviceName = getSelectedServiceDisplayName();
    if (!readyForNextService || !serviceEl?.value || !serviceMeta || !serviceName) {
      selectedServiceContextEl.classList.add("hidden");
      selectedServiceContextEl.innerHTML = "";
      return;
    }

    const categoryName = getSelectedServiceCategoryName();
    const summary = String(serviceMeta.summary || "").trim();
    const mn = Number(serviceMeta.labor_hours_min ?? 0);
    const mx = Number(serviceMeta.labor_hours_max ?? 0);
    const laborRange = mn > 0 && mx >= mn
      ? `Typical labor range: ${fmt1(mn)}-${fmt1(mx)} hrs`
      : "";
    const blueprintHref = buildRepairBlueprintHref(serviceEl.value, serviceName);
    const confidenceGuidance = getMechanicConfidenceGuidance({
      ...serviceMeta,
      serviceCode: serviceEl.value,
      serviceText: serviceName,
      categoryName,
    });

    selectedServiceContextEl.innerHTML = `
      <div class="selected-service-context__title">${escapeServiceResultHtml(serviceName)}${categoryName ? ` - ${escapeServiceResultHtml(categoryName)}` : ""}</div>
      ${summary ? `<div class="selected-service-context__summary">${escapeServiceResultHtml(summary)}</div>` : ""}
      ${laborRange ? `<div class="selected-service-context__meta">${escapeServiceResultHtml(laborRange)}</div>` : ""}
      <div class="selected-service-context__confidence">
        <span>${escapeServiceResultHtml(confidenceGuidance.cue)}</span>
        ${escapeServiceResultHtml(confidenceGuidance.priority)}
      </div>
      <div class="selected-service-context__momentum" aria-label="Pricing workflow">
        <span>Labor range</span>
        <span>Related checks</span>
        <span>Parts/travel</span>
        <span>Quote</span>
      </div>
      ${blueprintHref ? `<a class="selected-service-context__link" href="${escapeServiceResultHtml(blueprintHref)}">Open repair guide with vehicle context</a>` : ""}
      <div class="selected-service-context__note">Review pricing before adding the job.</div>
    `;
    selectedServiceContextEl.classList.remove("hidden");
  }

  async function apiJSON(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      const t = await r.text().catch(() => "");
      let detail = t;
      try {
        const parsed = JSON.parse(t);
        detail = parsed?.detail || parsed?.error || t;
      } catch (_) {}
      throw new Error(`${r.status} ${r.statusText} ${detail}`.trim());
    }
    return r.json();
  }

  function normalizeVinInput(value) {
    return String(value || "").replace(/\s+/g, "").trim().toUpperCase();
  }

  function normalizeVehicleOption(value) {
    return String(value || "").replace(/[^a-z0-9]/gi, "").toLowerCase();
  }

  function findVehicleOptionIndex(selectEl, value) {
    const exactNeedle = String(value || "").trim().toLowerCase();
    const normalizedNeedle = normalizeVehicleOption(value);
    if (!selectEl || (!exactNeedle && !normalizedNeedle)) return -1;

    for (let i = 0; i < selectEl.options.length; i++) {
      const option = selectEl.options[i];
      const optionValues = [
        option.text || "",
        option.textContent || "",
        option.value || "",
      ];
      if (optionValues.some(raw => String(raw).trim().toLowerCase() === exactNeedle)) {
        return i;
      }
      if (normalizedNeedle && optionValues.some(raw => normalizeVehicleOption(raw) === normalizedNeedle)) {
        return i;
      }
    }
    return -1;
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
          setCategoryValue(diagValue, "auto");

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
          setCategoryValue(diagCatValue, "auto");
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
  const customerQuoteFinalActions = $("customerQuoteFinalActions");
  const customerQuoteFinalHint = $("customerQuoteFinalHint");
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

  function refreshActiveVehicleControls() {
    const activeVehicleId = estimateState.activeVehicleId || estimateState.vehicles?.[0]?.id || "";
    const scopedSelector = (className) =>
      activeVehicleId
        ? document.querySelector(`.${className}[data-vehicle-id="${activeVehicleId}"]`)
        : document.querySelector(`.${className}`);

    yearEl = scopedSelector("vehicle-year") || yearEl;
    makeEl = scopedSelector("vehicle-make") || makeEl;
    modelEl = scopedSelector("vehicle-model") || modelEl;
  }

  // ---- Categories / services ----
  function hideServiceResults() {
    if (!serviceResults) return;
    serviceResults.style.display = "none";
    serviceResults.innerHTML = "";
  }

  function syncServiceSearchFromSelect() {
    if (!serviceSearch || !serviceEl) return;
    const selectedText = getSelectedServiceDisplayName();
    serviceSearch.value = serviceEl.value ? selectedText : "";
    updateServiceClearButton();
  }

  function setCategoryValue(value, source = "auto") {
    if (!categoryEl) return;
    categoryEl.value = value || "";
    categorySelectionSource = categoryEl.value ? source : "none";
    updateCategoryClearButton();
  }

  function hasManualCategoryFilter() {
    return !!(categoryEl?.value && categorySelectionSource === "manual");
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
    chain: ["timing chain", "timing chain kit", "timing chain tensioner", "chain tensioner"],
    timingchain: ["timing chain", "timing chain kit", "timing chain tensioner", "engine timing"],
    chaintensioner: ["timing chain tensioner", "chain tensioner", "startup rattle"],
    startuprattle: ["timing chain kit", "timing chain tensioner", "startup rattle"],
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
    radiatorfan: {
      codes: [
        "cooling_fan_assembly_replacement",
        "cooling_fan_motor_replacement",
        "cooling_fan_diagnosis",
      ],
      terms: ["radiator fan", "cooling fan", "fan motor", "overheating fan"],
    },
    coolingfan: {
      codes: [
        "cooling_fan_assembly_replacement",
        "cooling_fan_motor_replacement",
        "cooling_fan_diagnosis",
      ],
      terms: ["cooling fan", "radiator fan", "fan motor", "overheating fan"],
    },
    coolantpressure: {
      codes: [
        "cooling_system_pressure_test",
        "coolant_leak_diagnosis",
        "radiator_replacement",
        "radiator_hose_replacement",
      ],
      terms: ["coolant pressure", "pressure test", "cooling leak", "coolant leak"],
    },
    coolingleak: {
      codes: [
        "cooling_system_pressure_test",
        "coolant_leak_diagnosis",
        "radiator_replacement",
        "radiator_hose_replacement",
      ],
      terms: ["cooling leak", "coolant leak", "pressure test", "coolant pressure"],
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
    batterydrain: {
      codes: [
        "parasitic_draw_test",
        "electrical_diagnostic",
        "battery_replacement",
        "alternator_diagnosis",
      ],
      terms: ["battery drain", "parasitic draw", "overnight drain", "electrical drain", "dead battery overnight"],
    },
    parasiticdraw: {
      codes: [
        "parasitic_draw_test",
        "electrical_diagnostic",
        "battery_replacement",
      ],
      terms: ["parasitic draw", "draw test", "battery drain", "dead battery overnight"],
    },
    deadbatteryovernight: {
      codes: [
        "parasitic_draw_test",
        "electrical_diagnostic",
        "battery_replacement",
        "alternator_diagnosis",
      ],
      terms: ["dead battery overnight", "overnight drain", "battery drain", "parasitic draw"],
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
    wheelbearing: {
      codes: [
        "wheel_bearing_replacement_front",
        "wheel_bearing_replacement_rear",
        "wheel_hub_assembly_replacement_each",
      ],
      terms: ["wheel bearing", "hub assembly", "humming noise", "bearing noise", "wheel hub"],
    },
    hubassembly: {
      codes: [
        "wheel_hub_assembly_replacement_each",
        "wheel_bearing_replacement_front",
        "wheel_bearing_replacement_rear",
      ],
      terms: ["hub assembly", "wheel hub", "wheel bearing", "bearing noise"],
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
    swaybar: {
      codes: [
        "sway_bar_link_replacement",
        "sway_bar_bushing_replacement",
        "suspension_noise_diagnosis",
      ],
      terms: ["sway bar", "stabilizer link", "clunk noise", "front end noise"],
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
    timingchain: {
      codes: [
        "timing_chain_guide_replacement",
        "timing_chain_tensioner_replacement",
        "timing_chain_service",
      ],
      terms: ["timing chain", "chain", "rattling timing chain", "startup rattle", "chain tensioner", "engine timing"],
    },
    chain: {
      codes: [
        "timing_chain_guide_replacement",
        "timing_chain_tensioner_replacement",
        "timing_chain_service",
      ],
      terms: ["timing chain", "chain tensioner", "startup rattle", "engine timing"],
    },
    rattlingtimingchain: {
      codes: [
        "timing_chain_guide_replacement",
        "timing_chain_tensioner_replacement",
      ],
      terms: ["rattling timing chain", "startup rattle", "chain tensioner"],
    },
    startuprattle: {
      codes: [
        "timing_chain_guide_replacement",
        "timing_chain_tensioner_replacement",
      ],
      terms: ["startup rattle", "rattling timing chain", "chain tensioner"],
    },
    chaintensioner: {
      codes: [
        "timing_chain_tensioner_replacement",
        "timing_chain_guide_replacement",
      ],
      terms: ["chain tensioner", "timing chain tensioner", "startup rattle"],
    },
    enginetiming: {
      codes: [
        "timing_chain_guide_replacement",
        "timing_chain_tensioner_replacement",
        "timing_belt_replacement",
      ],
      terms: ["engine timing", "timing chain", "timing belt"],
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

  function getEstimateRiskNote(service = {}) {
    const serviceText = normalizeServiceSearch([
      service.serviceCode,
      service.serviceText,
      service.code,
      service.name,
      service.category,
      service.categoryName,
    ].join(" "));
    const vehicle = String(service.vehicleLabel || service.vehicleDisplayModel || service.vehicleModel || "").trim();
    const contextSuffix = vehicle
      ? " Procedure complexity may vary by engine, trim, drivetrain, and vehicle condition."
      : " Labor time may vary based on vehicle condition.";

    if (serviceText.includes("water pump")) {
      return `Inspect coolant condition, thermostat behavior, belt drive, and seized hardware risk before final approval.${contextSuffix}`;
    }

    if (serviceText.includes("alternator")) {
      return `Verify charging output, battery condition, belt tensioner, cables, and grounds before final approval.${contextSuffix}`;
    }

    if (serviceText.includes("starter")) {
      return `Verify battery condition, cable voltage drop, and starter circuit command before final approval.${contextSuffix}`;
    }

    const isBrakeService = [
      "brake pad",
      "brake pads",
      "brake rotor",
      "brake rotors",
      "brake caliper",
      "brake hardware",
      "wheel cylinder",
    ].some((term) => serviceText.includes(normalizeServiceSearch(term))) ||
      serviceText.includes("brake");

    if (isBrakeService) {
      return `Inspect rotor condition, caliper hardware, slide pins, and brake fluid condition before final approval.${contextSuffix}`;
    }

    return `Additional diagnostics or related system inspection may be required if access, corrosion, or vehicle condition changes the repair path.${contextSuffix}`;
  }

  function getMechanicConfidenceGuidance(service = {}) {
    const serviceText = normalizeServiceSearch([
      service.serviceCode,
      service.code,
      service.name,
      service.serviceText,
      service.category,
      service.categoryName,
    ].join(" "));

    const hasVehicle = Boolean(getVehicleDetails(getActiveVehicle()));
    const contextSuffix = hasVehicle ? " Access may vary by engine and drivetrain." : "";

    if (serviceText.includes("spark plug") || serviceText.includes("ignition coil") || serviceText.includes("misfire")) {
      return {
        priority: "Inspect ignition components first; verify fuel trims and vacuum leaks when evidence is mixed.",
        cue: "Multiple causes possible",
      };
    }

    if (serviceText.includes("water pump") || serviceText.includes("radiator") || serviceText.includes("thermostat") || serviceText.includes("coolant")) {
      return {
        priority: `Verify coolant level/condition first; pressure test if coolant loss or smell is present.${contextSuffix}`,
        cue: "Inspection recommended before replacement",
      };
    }

    if (serviceText.includes("starter") || serviceText.includes("battery") || serviceText.includes("alternator") || serviceText.includes("no crank")) {
      return {
        priority: "Verify battery voltage, cable drop, grounds, and command signal before pricing parts.",
        cue: "Further diagnostics may be required",
      };
    }

    if (serviceText.includes("brake")) {
      return {
        priority: "Inspect rotor condition, pad wear pattern, and caliper hardware movement before pad-only service.",
        cue: "Common repair when measurements support it",
      };
    }

    return {
      priority: `Confirm inspection evidence before replacement.${contextSuffix}`,
      cue: "Inspection recommended before replacement",
    };
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

  function isTimingChainSearchQuery(query) {
    const normalizedQuery = normalizeServiceSearch(query);
    const compactQuery = compactServiceSearch(query);
    return [
      "timing chain",
      "chain",
      "rattling timing chain",
      "startup rattle",
      "chain tensioner",
      "timing chain tensioner",
    ].some((term) => normalizedQuery.includes(normalizeServiceSearch(term)) || compactQuery.includes(compactServiceSearch(term)));
  }

  async function ensureAllServiceOptions(query = "") {
    const vehicleKey = getServiceSearchVehicleKey();
    const queryKey = isTimingChainSearchQuery(query) ? "timing-chain" : "default";
    const cacheKey = `${vehicleKey}|${queryKey}`;
    if (allServiceOptions.length && allServiceOptionsVehicleKey === cacheKey) {
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
            await apiJSON(`/api/services/${encodeURIComponent(categoryKey)}`),
            { query }
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
    allServiceOptionsVehicleKey = cacheKey;
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

  function hideQuoteCompletionSuggestions() {
    completionSuggestions?.classList.add("hidden");
    if (completionSuggestionsList) completionSuggestionsList.innerHTML = "";
  }

  function hideEstimatorWorkflowShortcuts() {
    estimatorWorkflowShortcuts?.classList.add("hidden");
    if (estimatorWorkflowShortcutList) estimatorWorkflowShortcutList.innerHTML = "";
  }

  function getPairedSuggestionConfig(lineItem) {
    const source = normalizeServiceSearch(`${lineItem?.serviceText || ""} ${lineItem?.serviceCode || ""}`);
    if (!source) return null;
    return COMMONLY_ADDED_TOGETHER.find((group) =>
      group.match.some((term) => source.includes(normalizeServiceSearch(term)))
    ) || null;
  }

  function getPairedSuggestionConfigs(lineItem) {
    const source = normalizeServiceSearch(`${lineItem?.serviceText || ""} ${lineItem?.serviceCode || ""}`);
    if (!source) return [];
    return COMMONLY_ADDED_TOGETHER
      .map((group, index) => {
        const matchedTerms = group.match.filter((term) => source.includes(normalizeServiceSearch(term)));
        if (!matchedTerms.length) return null;
        const bestTermLength = Math.max(...matchedTerms.map((term) => normalizeServiceSearch(term).length));
        return {
          ...group,
          index,
          score: bestTermLength + Math.max(0, 20 - index),
        };
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
  }

  function getActivePairedSuggestionSource() {
    if (readyForNextService && serviceEl?.value) {
      return {
        serviceCode: serviceEl.value,
        serviceText: getSelectedServiceName(),
      };
    }

    return lineItems[lineItems.length - 1] || null;
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

  function getVisiblePairedSuggestionCodes() {
    if (!pairedSuggestionsList) return new Set();
    return new Set(
      Array.from(pairedSuggestionsList.querySelectorAll("[data-service-code]"))
        .map((el) => String(el.dataset.serviceCode || "").trim())
      .filter(Boolean)
    );
  }

  function getWorkflowShortcutSource() {
    if (serviceEl?.value) {
      return {
        serviceCode: serviceEl.value,
        serviceText: getSelectedServiceName(),
      };
    }

    return lineItems[lineItems.length - 1] || null;
  }

  function getShortcutKind(stage = "", label = "") {
    const source = normalizeServiceSearch(`${stage} ${label}`);
    if (source.includes("inspect") || source.includes("test") || source.includes("check") || source.includes("diagnos")) {
      return "inspection";
    }
    return "repair";
  }

  function shortcutActionLabel(kind) {
    return kind === "inspection" ? "Inspect Related" : "Add Repair";
  }

  function buildWorkflowShortcuts(suggestions, source) {
    const shortcuts = [];
    suggestions.slice(0, 2).forEach(({ label, option, stage, reason }) => {
      const kind = getShortcutKind(stage, label || option?.name);
      shortcuts.push({
        type: "service",
        kind,
        label: label || option?.name || "Related job",
        meta: reason || getServiceHelperText(option),
        action: shortcutActionLabel(kind),
        serviceCode: option?.code || "",
        category: option?.category || "",
      });
    });

    const blueprintHref = buildRepairBlueprintHref(source?.serviceCode, source?.serviceText);
    if (blueprintHref) {
      shortcuts.push({
        type: "link",
        kind: "blueprint",
        label: "Related Repair Guide",
        meta: "Open with this vehicle and service context.",
        action: "Repair Path",
        href: blueprintHref,
      });
    }

    const systemHref = buildSystemHubHref(source?.serviceCode, source?.serviceText);
    if (systemHref) {
      shortcuts.push({
        type: "link",
        kind: "system",
        label: "Related System Hub",
        meta: "Inspect symptoms, codes, and workflows for this system.",
        action: "System Check",
        href: systemHref,
      });
    }

    return shortcuts.slice(0, 4);
  }

  function renderWorkflowShortcuts(shortcuts) {
    if (!estimatorWorkflowShortcuts || !estimatorWorkflowShortcutList) return;
    if (!shortcuts.length) {
      hideEstimatorWorkflowShortcuts();
      return;
    }

    estimatorWorkflowShortcutList.innerHTML = shortcuts.map((item) => {
      const content = `
        <span>
          <small>${escapeServiceResultHtml(item.action || "Open")}</small>
          <strong>${escapeServiceResultHtml(item.label || "Workflow shortcut")}</strong>
          <em>${escapeServiceResultHtml(item.meta || "")}</em>
        </span>
      `;

      if (item.type === "link") {
        return `
          <a class="tm-estimator-workflow-shortcut" data-kind="${escapeServiceResultHtml(item.kind || "link")}" href="${escapeServiceResultHtml(item.href || "#")}">
            ${content}
          </a>
        `;
      }

      return `
        <button
          type="button"
          class="tm-estimator-workflow-shortcut"
          data-kind="${escapeServiceResultHtml(item.kind || "service")}"
          data-service-code="${escapeServiceResultHtml(item.serviceCode || "")}"
          data-service-category="${escapeServiceResultHtml(item.category || "")}"
        >
          ${content}
        </button>
      `;
    }).join("");

    estimatorWorkflowShortcuts.classList.remove("hidden");
  }

  function buildRelatedRepairSuggestions(groups, options, existingCodes, limit = 3) {
    const bestByCode = new Map();

    groups.forEach((group, groupIndex) => {
      (group.suggestions || group.reminders || []).forEach((rawSuggestion, suggestionIndex) => {
        const option = findPairedServiceOption(rawSuggestion, options);
        const code = option?.code;
        if (!code || existingCodes.has(code)) return;

        const score = Number(rawSuggestion.weight || 50) + Number(group.score || 0) - suggestionIndex - groupIndex;
        const suggestion = {
          ...rawSuggestion,
          option,
          score,
          context: rawSuggestion.context || group.context || "",
          stage: rawSuggestion.stage || "Related job",
          reason: rawSuggestion.reason || getServiceHelperText(option),
        };
        const previous = bestByCode.get(code);
        if (!previous || suggestion.score > previous.score) {
          bestByCode.set(code, suggestion);
        }
      });
    });

    return Array.from(bestByCode.values())
      .sort((a, b) => b.score - a.score || String(a.label || a.option.name).localeCompare(String(b.label || b.option.name)))
      .slice(0, limit);
  }

  async function refreshPairedSuggestions() {
    if (!pairedSuggestions || !pairedSuggestionsList) return;
    const suggestionSource = getActivePairedSuggestionSource();
    if (!suggestionSource) {
      hidePairedSuggestions();
      void refreshEstimatorWorkflowShortcuts();
      return;
    }

    const configs = getPairedSuggestionConfigs(suggestionSource);
    if (!configs.length) {
      hidePairedSuggestions();
      void refreshEstimatorWorkflowShortcuts();
      return;
    }

    let options = [];
    try {
      options = await ensureAllServiceOptions();
    } catch (err) {
      console.warn("Commonly added service lookup failed", err);
      hidePairedSuggestions();
      void refreshEstimatorWorkflowShortcuts();
      return;
    }

    const existingCodes = new Set(lineItems.map((it) => it.serviceCode).filter(Boolean));
    if (serviceEl?.value) existingCodes.add(serviceEl.value);
    const suggestions = buildRelatedRepairSuggestions(configs, options, existingCodes, 3);

    if (!suggestions.length) {
      hidePairedSuggestions();
      void refreshEstimatorWorkflowShortcuts();
      return;
    }

    pairedSuggestionsList.innerHTML = suggestions.map(({ label, option, stage, reason, context }) => `
      <button
        type="button"
        class="tm-paired-suggestion"
        data-service-code="${escapeServiceResultHtml(option.code)}"
        data-service-category="${escapeServiceResultHtml(option.category || "")}"
        title="${escapeServiceResultHtml(reason || "")}"
      >
        <span>
          <strong>${escapeServiceResultHtml(label || option.name)}</strong>
          <em>${escapeServiceResultHtml(reason || context || getServiceHelperText(option))}</em>
        </span>
        <small>${escapeServiceResultHtml(stage || "Related")}</small>
      </button>
    `).join("");

    renderWorkflowShortcuts(buildWorkflowShortcuts(suggestions, suggestionSource));
    pairedSuggestions.classList.add("hidden");
  }

  async function refreshEstimatorWorkflowShortcuts() {
    if (!estimatorWorkflowShortcuts || !estimatorWorkflowShortcutList) return;
    const source = getWorkflowShortcutSource();
    if (!source) {
      hideEstimatorWorkflowShortcuts();
      return;
    }

    const configs = getPairedSuggestionConfigs(source);
    let suggestions = [];
    if (configs.length) {
      try {
        const options = await ensureAllServiceOptions();
        const existingCodes = new Set(lineItems.map((it) => it.serviceCode).filter(Boolean));
        if (serviceEl?.value) existingCodes.add(serviceEl.value);
        suggestions = buildRelatedRepairSuggestions(configs, options, existingCodes, 2);
      } catch (err) {
        console.warn("Workflow shortcut lookup failed", err);
      }
    }

    const shortcuts = buildWorkflowShortcuts(suggestions, source);
    renderWorkflowShortcuts(shortcuts);
  }

  async function refreshQuoteCompletionSuggestions() {
    if (!completionSuggestions || !completionSuggestionsList) return;
    if (!lineItems.length) {
      hideQuoteCompletionSuggestions();
      return;
    }

    let options = [];
    try {
      options = await ensureAllServiceOptions();
    } catch (err) {
      console.warn("Quote completion service lookup failed", err);
      hideQuoteCompletionSuggestions();
      return;
    }

    const quoteText = normalizeServiceSearch(
      lineItems.map((it) => `${it.serviceText || ""} ${it.serviceCode || ""}`).join(" ")
    );
    const existingCodes = new Set(lineItems.map((it) => it.serviceCode).filter(Boolean));
    if (serviceEl?.value) existingCodes.add(serviceEl.value);

    getVisiblePairedSuggestionCodes().forEach((code) => existingCodes.add(code));
    const matchingGroups = QUOTE_COMPLETION_CHECKS
      .map((group, index) => {
        const matched = group.match.some((term) => quoteText.includes(normalizeServiceSearch(term)));
        return matched ? { ...group, index, score: Math.max(0, 16 - index) } : null;
      })
      .filter(Boolean);
    const reminders = buildRelatedRepairSuggestions(matchingGroups, options, existingCodes, 4);

    if (!reminders.length) {
      hideQuoteCompletionSuggestions();
      return;
    }

    completionSuggestionsList.innerHTML = reminders.map(({ label, option, stage, reason }) => `
      <button
        type="button"
        class="tm-completion-suggestion"
        data-service-code="${escapeServiceResultHtml(option.code)}"
        data-service-category="${escapeServiceResultHtml(option.category || "")}"
        title="${escapeServiceResultHtml(reason || "")}"
      >
        <span>
          <strong>${escapeServiceResultHtml(label || option.name)}</strong>
          <em>${escapeServiceResultHtml(reason || getServiceHelperText(option))}</em>
        </span>
        <small>${escapeServiceResultHtml(stage || "Review")}</small>
      </button>
    `).join("");

    completionSuggestions.classList.remove("hidden");
  }

  async function selectPairedSuggestion(serviceCode, categoryKey) {
    if (!serviceCode || !categoryEl || !serviceEl) return;
    if (lineItems.some((it) => it.serviceCode === serviceCode)) {
      setStatus("info", "That related job is already on this quote.");
      void refreshPairedSuggestions();
      void refreshQuoteCompletionSuggestions();
      return;
    }

    if (!readyForNextService) {
      addLineBtn?.click();
    }

    if (categoryKey && categoryEl.value !== categoryKey) {
      setCategoryValue(categoryKey, "auto");
      await loadServices(categoryKey);
    }

    applyServiceSelection(serviceCode);
    await loadServiceMeta(serviceCode);
    readyForNextService = true;
    updateEstimateButtonState();
    void refreshPairedSuggestions();

    const serviceName = serviceEl.options[serviceEl.selectedIndex]?.textContent?.trim() || "Suggested job";
    setStatus("info", `${serviceName} selected. Review pricing, then add it to the quote.`);
  }

  async function selectQuickQuoteShortcut(shortcutKey, { addToQuote = false } = {}) {
    const shortcut = QUICK_QUOTE_SHORTCUTS[shortcutKey];
    if (!shortcut || !categoryEl || !serviceEl) return;

    if (activeEditingLineId) {
      setStatus("error", "Save or cancel the current line edit before using a quick repair shortcut.");
      return;
    }

    if (!readyForNextService) {
      addLineBtn?.click();
    }

    if (categoryEl.value !== shortcut.category) {
      setCategoryValue(shortcut.category, "auto");
      await loadServices(shortcut.category);
    }

    applyServiceSelection(shortcut.serviceCode);
    await loadServiceMeta(shortcut.serviceCode);
    readyForNextService = true;
    hidePairedSuggestions();
    updateEstimateButtonState();
    if (!addToQuote) void refreshPairedSuggestions();

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

    if (addToQuote && estimateBtn && !estimateBtn.disabled) {
      estimateBtn.click();
    } else if (addToQuote) {
      estimateBtn?.focus();
    }
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

    const shouldSearchAllServices = !hasManualCategoryFilter();
    const searchOptions =
      shouldSearchAllServices && allServiceOptions.length ? allServiceOptions : serviceOptions;
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

  function resetServiceSearch({ placeholder = SERVICE_SEARCH_PLACEHOLDER, disabled = false } = {}) {
    serviceOptions = [];
    if (!serviceSearch) return;
    serviceSearch.value = "";
    serviceSearch.placeholder = placeholder;
    serviceSearch.disabled = disabled;
    hideServiceResults();
    updateServiceClearButton();
  }

  function enableServiceSearch() {
    if (!serviceSearch) return;
    serviceSearch.disabled = false;
    serviceSearch.placeholder = SERVICE_SEARCH_PLACEHOLDER;
    updateServiceClearButton();
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

  async function clearServiceSelection({ focus = true } = {}) {
    if (!serviceEl) return;
    serviceEl.value = "";
    if (serviceSearch) {
      serviceSearch.value = "";
      serviceSearch.placeholder = SERVICE_SEARCH_PLACEHOLDER;
    }
    hideServiceResults();
    hidePairedSuggestions();
    serviceMeta = null;
    editingLineItem = null;
    activeEditingLineId = null;
    readyForNextService = true;
    document.querySelectorAll(".tm-quick-quote").forEach((btn) => btn.classList.remove("is-selected"));
    await loadServiceMeta("");
    updateServiceClearButton();
    updateEstimateButtonState();
    if (focus && serviceSearch && !serviceSearch.disabled) {
      serviceSearch.focus({ preventScroll: true });
    }
  }

  function getTimingSystemConfig(vehicle) {
    const make = String(vehicle?.make || "").trim().toUpperCase();
    const model = String(vehicle?.model || "").trim().toUpperCase();

    if (!make || !model) return null;
    return TIMING_SYSTEM_SUPPORT[make]?.[model] || null;
  }

  function filterServicesForActiveVehicle(svcs, { query = "" } = {}) {
    const services = Array.isArray(svcs) ? [...svcs] : [];
    const timingConfig = getTimingSystemConfig(getActiveVehicle());
    const hiddenTimingCodes = new Set([TIMING_SERVICE_CODES.timingChainLegacy]);
    const timingChainQuery = isTimingChainSearchQuery(query);

    if (!timingConfig) {
      if (timingChainQuery) {
        hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingBelt);
      } else {
        hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingChainKit);
        hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingChainTensioner);
      }
      return services.filter((service) => !hiddenTimingCodes.has(service.code));
    }

    if (timingConfig.type === "chain") {
      hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingBelt);
      return services.filter((service) => !hiddenTimingCodes.has(service.code));
    }

    if (timingConfig.type === "belt") {
      hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingChainKit);
      hiddenTimingCodes.add(TIMING_SERVICE_CODES.timingChainTensioner);
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
    renderSelectedServiceContext();

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
      renderSelectedServiceContext();
      return;
    }

    serviceMeta = await apiJSON(`/api/service/${encodeURIComponent(serviceCode)}`);

    updateLaborRangeUI();
    renderSelectedServiceContext();

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
    } else {
      reinitializeSignatureCanvas();
    }
    refreshApprovalStatus();
  }

  function getApprovalStatusCopy() {
    const wantsSignature = getWantSig() === "yes";
    const reviewed = !!customerAgreesChk?.checked;
    let signed = false;

    try {
      signed = wantsSignature && !!sigCanvas && !canvasIsBlank();
    } catch (_) {
      signed = false;
    }

    if (wantsSignature && signed) {
      return {
        state: "signed",
        title: "Signed customer approval",
        detail: "The PDF will show that the customer reviewed and approved the estimate. No payment is collected or recorded.",
      };
    }

    if (wantsSignature) {
      return {
        state: "signature-needed",
        title: "Signature required before PDF",
        detail: "The customer must sign in the box below, or choose the no-signature PDF option.",
      };
    }

    if (reviewed) {
      return {
        state: "reviewed",
        title: "Customer reviewed estimate",
        detail: "The PDF will show that the customer reviewed the estimate. No payment is collected or recorded.",
      };
    }

    return {
      state: "prepared",
      title: "Prepared estimate",
      detail: "The PDF will be prepared for customer review, but it will not be marked reviewed or approved.",
    };
  }

  function refreshApprovalStatus() {
    if (!approvalStatusEl) return;
    const status = getApprovalStatusCopy();
    approvalStatusEl.dataset.state = status.state;
    approvalStatusEl.innerHTML = `<strong>${status.title}</strong><span>${status.detail}</span>`;
  }

  document.querySelectorAll('input[name="wantSig"]').forEach((el) => {
    el.addEventListener("change", () => {
      setSigVisible(getWantSig() === "yes");
      if (getWantSig() === "no" && confirmMsg) {
        clearConfirmMessage();
      }
      refreshApprovalStatus();
    });
  });

  function openConfirm() {
    if (!confirmModal) return false;

    if (hasOpenLineEdit()) {
      setStatus("error", "Save the current line edit before creating the customer quote.");
      focusOpenLineEdit();
      return false;
    }

    if (isAddingLineItem || isGeneratingAllLines) {
      setStatus("info", "Finish the current quote update before opening the customer quote.");
      return false;
    }

    clearConfirmMessage();
    confirmModal.classList.remove("hidden");
    confirmModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    window.TorqueMechClearFields?.refresh(confirmModal);

    setSigVisible(getWantSig() === "yes");

    const listEl = document.getElementById("confirmServicesList");
    if (listEl) {
      const total = lineItems.reduce((sum, it) => sum + Number(it.estimate || 0), 0);
      const outputOptions = getCustomerOutputOptions();

      listEl.innerHTML = `
        <div class="tm-confirm-services-list">
          <div class="tm-confirm-total-band">
            <span>Prepared estimate summary</span>
            <strong>${money(total)}</strong>
          </div>
          ${lineItems.map((it) => {
            const vehicleLabel = getCustomerVehicleLabel(it.vehicleLabel || getActiveVehicle());
            const serviceTotal = it.estimate != null ? money(it.estimate) : "Pending";
            const serviceName = escapeServiceResultHtml(it.serviceText || "Service");
            const pricingMeta = getVisibleLineItemPricingMeta(it, outputOptions);
            return `
            <div class="tm-confirm-service-row">
              <div class="tm-confirm-service-main">
                <div class="tm-confirm-service-name">${serviceName}</div>
                <div class="tm-confirm-service-status" data-status="${normalizeRepairStatus(it.status)}">
                  Status: ${getRepairStatusLabel(it.status)}
                </div>
                <div class="tm-confirm-service-vehicle">
                  ${escapeServiceResultHtml(vehicleLabel)}
                </div>
                ${pricingMeta.length ? `
                  <div class="tm-confirm-service-breakdown" aria-label="Line item pricing">
                    ${pricingMeta.map(item => `
                      <span data-kind="${escapeServiceResultHtml(item.kind || "")}" class="${item.empty ? "is-empty" : ""}">
                        <strong>${escapeServiceResultHtml(item.label)}</strong>
                        <em>${escapeServiceResultHtml(item.value)}</em>
                      </span>
                    `).join("")}
                  </div>
                ` : ""}
                <div class="tm-confirm-service-note">
                  ${escapeServiceResultHtml(getEstimateRiskNote(it))}
                </div>
              </div>
              <div class="tm-confirm-service-total">
                <span>Estimate</span>
                <strong>${serviceTotal}</strong>
              </div>
            </div>
          `}).join("")}
          <div class="tm-confirm-grand-total">
            <div>Ready for customer review</div>
            <strong>${money(total)}</strong>
          </div>
        </div>
      `;
    }

    refreshQuotePreview();
    refreshQuoteIdentityNudge();
    refreshApprovalStatus();
    resizeSigCanvas();
    return true;
  }

  function closeConfirm(force = false) {
    if (isGeneratingCustomerPdf && !force) return;

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

  customerNameEl?.addEventListener("input", () => {
    refreshQuotePreview();
    refreshQuoteIdentityNudge();
  });
  customerPhoneEl?.addEventListener("input", refreshQuotePreview);
  customerAgreesChk?.addEventListener("change", refreshApprovalStatus);
  notesEl?.addEventListener("input", refreshQuotePreview);
  pdfShowGeneratedDateChk?.addEventListener("change", refreshQuotePreview);
  pdfShowHourlyRateChk?.addEventListener("change", () => {
    renderLineItems();
    refreshQuotePreview();
  });
  function refreshLineItemVisibilityPreviews() {
    renderLineItems();
    if (confirmModal && !confirmModal.classList.contains("hidden")) {
      openConfirm();
    }
  }

  pdfShowLaborColumnChk?.addEventListener("change", refreshLineItemVisibilityPreviews);
  pdfShowPartsColumnChk?.addEventListener("change", refreshLineItemVisibilityPreviews);
  pdfShowRiskNotesChk?.addEventListener("change", refreshQuotePreview);
  pdfShowInspectionFindingsChk?.addEventListener("change", refreshQuotePreview);
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
    sigCtx = ctx;

    ctx.clearRect(0, 0, sigCanvas.width, sigCanvas.height);

    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = signatureInkColor();
  }

  function resizeSigCanvas({ preserve = true } = {}) {
    if (!sigCanvas) return false;

    const rect = sigCanvas.getBoundingClientRect();
    const width = Math.round(rect.width);
    const height = Math.round(rect.height);
    if (width <= 0 || height <= 0) return false;

    const prevData = preserve && sigCanvas.width > 0 && sigCanvas.height > 0
      ? sigCanvas.toDataURL()
      : ""; // preserve drawing

    sigCanvas.width = width;
    sigCanvas.height = height;

    sigCtx = sigCanvas.getContext("2d");

    sigCtx.lineWidth = 2;
    sigCtx.lineCap = "round";
    sigCtx.lineJoin = "round";
    sigCtx.strokeStyle = signatureInkColor();

    // restore previous drawing
    if (prevData) {
      const img = new Image();
      img.onload = () => sigCtx.drawImage(img, 0, 0, width, height);
      img.src = prevData;
    }
    return true;
  }

  function reinitializeSignatureCanvas() {
    if (!sigCanvas) return;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        resizeSigCanvas({ preserve: false });
        clearSignatureCanvas();
        refreshApprovalStatus();
      });
    });
  }

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    signatureDataUrl = null;
    clearSignatureCanvas();
  });

  function canvasIsBlank() {
    if (!sigCanvas) return true;
    if (sigCanvas.width <= 0 || sigCanvas.height <= 0) return true;
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

  function endDraw() {
    if (!isDrawing) return;
    isDrawing = false;
    signatureDataUrl = null;
    refreshApprovalStatus();
  }

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
      refreshApprovalStatus();
    });
  }
  // ---- Build request ----
  function buildRequest(extra = {}) {
    const activeVehicle = getActiveVehicle() || {};

    return {
      year: Number(activeVehicle.year || 0),
      make: (activeVehicle.make || "").trim(),
      model: (activeVehicle.model || "").trim(),
      displayModel: getVehicleDisplayModel(activeVehicle),
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
    if (!lineItems.length) hideQuoteCompletionSuggestions();
    const outputOptions = getCustomerOutputOptions();

    lineItemsList.innerHTML = lineItems
      .map((it, idx) => {
        if (!it.id) {
          it.id = createLineItemId();
        }
        it.status = normalizeRepairStatus(it.status);
        const est = it.estimate != null ? money(it.estimate) : "—";
        const statusLabel = getRepairStatusLabel(it.status);

        const pricingMeta = getVisibleLineItemPricingMeta(it, outputOptions);

        const hasBreakdown =
          outputOptions.showDetailedLaborBreakdown &&
          it.laborBreakdown &&
          Array.isArray(it.laborBreakdown.steps) &&
          it.laborBreakdown.steps.length > 0;
        const lineItemId = it.id || "";
        const riskNote = escapeServiceResultHtml(getEstimateRiskNote(it));
        const inspectionFindings = escapeServiceResultHtml(it.inspectionFindings || "");
        const isActiveEdit = activeEditingLineId === lineItemId;
        const isPriced = it.estimate != null;

        return `
          <div class="tm-service-card${isActiveEdit ? " is-editing" : ""}${isPriced ? "" : " is-unpriced"}" data-idx="${idx}" data-line-item-id="${lineItemId}">
            <div class="tm-service-head">
              <div class="tm-service-head-main">
                <div class="tm-service-title-row">
                  <div class="tm-service-title">${it.serviceText || "Service"}</div>
                  <label class="tm-repair-status-control" data-status="${it.status}">
                    <span>Repair Status</span>
                    <select
                      data-action="repair-status"
                      data-line-item-id="${lineItemId}"
                      aria-label="Repair Status for ${escapeServiceResultHtml(it.serviceText || "service")}"
                    >
                      ${renderRepairStatusOptions(it.status)}
                    </select>
                  </label>
                  ${isActiveEdit ? `<span class="tm-service-editing-pill">Editing</span>` : ""}
                </div>
                <div class="tm-repair-status-summary" data-status="${it.status}">Status: ${escapeServiceResultHtml(statusLabel)}</div>
                <div class="tm-service-vehicle">${getCustomerVehicleLabel(it.vehicleLabel || getActiveVehicle())}</div>
                <div class="tm-service-meta">
                  ${pricingMeta.map(item => `
                    <span class="${item.empty ? "is-empty" : ""}" data-kind="${escapeServiceResultHtml(item.kind || "")}">
                      <strong>${escapeServiceResultHtml(item.label)}</strong>
                      <em>${escapeServiceResultHtml(item.value)}</em>
                    </span>
                  `).join("")}
                </div>
                ${renderCostBreakdownHtml(it, outputOptions)}
                <div class="tm-service-risk-note">
                  <span class="tm-service-risk-label">Estimate note</span>
                  <span>${riskNote}</span>
                </div>
                <label class="tm-inspection-findings">
                  <span>Inspection Findings</span>
                  <textarea
                    data-action="inspection-findings"
                    data-line-item-id="${lineItemId}"
                    rows="2"
                    maxlength="240"
                    placeholder="Add a short inspection finding..."
                  >${inspectionFindings}</textarea>
                </label>
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

              <button type="button" class="tm-btn tm-btn-secondary tm-service-action-recalc" data-action="estimate" data-line-item-id="${lineItemId}">
                Recalculate
              </button>

              <button type="button" class="tm-btn tm-btn-secondary tm-service-action-edit" data-action="edit-line" data-line-item-id="${lineItemId}">
                Edit
              </button>

              <button type="button" class="tm-btn tm-btn-danger tm-service-action-remove" data-action="remove" data-line-item-id="${lineItemId}">
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
    lineItemsList.querySelectorAll('[data-action="inspection-findings"]').forEach((input) => {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
    });
    updateEstimateButtonState();
    void refreshQuoteCompletionSuggestions();
  }

  function getLineItemCardById(lineItemId) {
    if (!lineItemsList || !lineItemId) return null;
    return Array.from(lineItemsList.querySelectorAll(".tm-service-card"))
      .find((card) => card.dataset.lineItemId === lineItemId) || null;
  }

  function scrollLineItemIntoView(lineItemId) {
    const run = () => {
      const card = getLineItemCardById(lineItemId);
      if (!card) return;

      card.scrollIntoView({
        behavior: "smooth",
        block: "center",
        inline: "nearest",
      });

      card.classList.remove("is-newly-added");
      void card.offsetWidth;
      card.classList.add("is-newly-added");
      window.setTimeout(() => {
        card.classList.remove("is-newly-added");
      }, 1400);
    };

    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(run);
    } else {
      window.setTimeout(run, 0);
    }
  }

  function getServiceAddSection() {
    return document.querySelector('section[aria-label="Service"]');
  }

  function highlightServiceAddArea() {
    const section = getServiceAddSection();
    if (!section) return;

    section.classList.remove("tm-service-add-focus");
    void section.offsetWidth;
    section.classList.add("tm-service-add-focus");
    window.setTimeout(() => {
      section.classList.remove("tm-service-add-focus");
    }, 1200);
  }

  function focusServiceAddArea() {
    const section = getServiceAddSection();
    const target = serviceSearch && !serviceSearch.disabled ? serviceSearch : section;
    target?.scrollIntoView({
      behavior: "smooth",
      block: window.innerWidth < 700 ? "center" : "start",
      inline: "nearest",
    });
    highlightServiceAddArea();

    window.setTimeout(() => {
      if (serviceSearch && !serviceSearch.disabled) {
        serviceSearch.focus({ preventScroll: true });
        return;
      }
      if (categoryEl && !categoryEl.value) {
        categoryEl.focus({ preventScroll: true });
        return;
      }
      serviceEl?.focus({ preventScroll: true });
    }, 260);
  }

  function focusAddAnotherRepair() {
    if (!addLineBtn || addLineBtn.hidden || addLineBtn.disabled) return;
    addLineBtn.focus({ preventScroll: true });
  }

  function syncWorkflowAccelerationState({ hasBasics = false, hasSelection = false, readyForNext = false } = {}) {
    const serviceSection = getServiceAddSection();
    const hasLines = lineItems.length > 0;
    document.body?.classList.toggle("tm-quote-has-lines", hasLines);
    serviceSection?.classList.toggle("is-ready-for-service", Boolean(readyForNext && hasBasics));
    serviceSection?.classList.toggle("is-locked-after-add", Boolean(hasLines && !readyForNext));
    document.querySelector(".tm-addServiceRow")?.classList.toggle("is-ready-next-repair", Boolean(hasLines && !readyForNext));
    document.querySelector(".tm-estimate-action-panel")?.classList.toggle("is-ready-to-add", Boolean(readyForNext && hasBasics && hasSelection));
    document.querySelector(".tm-estimate-action-panel")?.classList.toggle("is-quote-next", Boolean(hasLines && !readyForNext));
  }

  function syncCustomerQuoteActionState() {
    const hasLines = lineItems.length > 0;
    if (generateAllBtn) generateAllBtn.disabled = isGeneratingAllLines || !hasLines;
    if (convertToProJobBtn) convertToProJobBtn.disabled = isGeneratingAllLines || !hasLines;
    customerQuoteFinalActions?.classList.toggle("is-disabled", !hasLines);
    if (customerQuoteFinalHint) {
      customerQuoteFinalHint.textContent = hasLines
        ? "Includes services, pricing, notes, and total."
        : "Add at least one service to enable customer quote creation.";
    }
  }

  function setServiceAddFieldsLocked(isLocked) {
    if (categoryEl) categoryEl.disabled = isLocked;
    if (serviceEl) serviceEl.disabled = isLocked;
    if (serviceSearch) {
      serviceSearch.disabled = isLocked;
      serviceSearch.placeholder = isLocked
        ? "Tap + Add Another Repair to add the next job."
        : SERVICE_SEARCH_PLACEHOLDER;
    }
    if (isLocked) hideServiceResults();
    updateServiceClearButton();
  }

  function updateEstimateButtonState() {
    if (!estimateBtn) return;

    const activeVehicle = getActiveVehicle() || {};
    const hasBasics = !!(activeVehicle.year && activeVehicle.make && activeVehicle.model);
    const hasSelection = !!serviceEl?.value;
    const isEditingSavedLine = !!activeEditingLineId;
    const isServiceAddLocked = !readyForNextService && !isEditingSavedLine;

    setServiceAddFieldsLocked(isServiceAddLocked);
    syncWorkflowAccelerationState({ hasBasics, hasSelection, readyForNext: readyForNextService });
    renderSelectedServiceContext();

    // --- Add Service button label (dynamic) ---
    if (addLineBtn) {
      // If user already added a service (locked state), guide them to add another
      addLineBtn.textContent = "+ Add Another Repair";
      addLineBtn.hidden = isEditingSavedLine || readyForNextService;
    }

    estimateBtn.disabled = isAddingLineItem
      ? true
      : isEditingSavedLine ? false : !(hasBasics && hasSelection && readyForNextService);
    estimateBtn.textContent = isEditingSavedLine
      ? "Save Line Changes"
      : editingLineItem ? "Update Quote Line" : "Add Service to Quote";


// Hint text: when locked, explain the flow
if (addServiceHint) {
  addServiceHint.hidden = isEditingSavedLine ? false : !!readyForNextService;
  if (isEditingSavedLine) {
    addServiceHint.textContent = "Editing line. Save changes.";
  } else {
    addServiceHint.textContent = "Add the next repair.";
  }
}
if (getEstimateHint) {
  getEstimateHint.hidden = true;
}

    if (workflowStepText) {
      workflowStepText.textContent = isEditingSavedLine
        ? "Editing line. Save changes."
        : !hasBasics
        ? "Next: select vehicle."
        : !hasSelection
          ? "Next: choose repair."
          : !readyForNextService
            ? "Add another repair or create quote."
            : "Review pricing, add job.";
    }

    // Add Another Service enabled ONLY after a service has been added
    if (addLineBtn) addLineBtn.disabled = isAddingLineItem || isEditingSavedLine || readyForNextService;
    if (saveDraftBtn) saveDraftBtn.disabled = isAddingLineItem || isGeneratingAllLines || !lineItems.length;
    syncCustomerQuoteActionState();

    // keep status helpful, but don't spam over error messages
    if (isEditingSavedLine) setStatus("info", "Editing line. Save changes.");
    else if (!hasBasics) setStatus("info", "Next: select vehicle.");
    else if (!hasSelection) setStatus("info", "Next: choose repair.");
    else if (!readyForNextService) setStatus("info", "Add another repair or create quote.");
    else setStatus("info", "Review pricing, add job.");
  }

  // ---- Add Service to Estimate FIRST ----
  estimateBtn?.addEventListener("click", async () => {
    if (isAddingLineItem) return;

    // Google Analytics event
    if (typeof gtag === "function") {
      gtag("event", "get_estimate_clicked", {
        event_category: "engagement",
        event_label: "Estimator Tool"
      });
    }

    if (activeEditingLineId) {
      isAddingLineItem = true;
      const it = getLineItemById(activeEditingLineId);
      if (!it) {
        console.warn("Save Line Changes ignored: active line item not found", {
          activeEditingLineId,
        });
        activeEditingLineId = null;
        isAddingLineItem = false;
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
        scrollLineItemIntoView(it.id);
        void refreshPairedSuggestions();
        updateEstimateButtonState();
        focusAddAnotherRepair();
        setStatus("ok", `${it.serviceText}: ${money(it.estimate)}. Line updated.`);
      } catch (e) {
        setStatus("error", `Save line failed: ${e.message}`);
      } finally {
        isAddingLineItem = false;
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
      if (!serviceEl.value) {
        setStatus("error", "Select a service.");
        return;
      }
    }

    // lock immediately so user can’t spam add
    isAddingLineItem = true;
    readyForNextService = false;
    updateEstimateButtonState();

    const rawServiceText = editingLineItem
      ? (editingLineItem.serviceText || editingLineItem.serviceCode)
      : (serviceEl.options[serviceEl.selectedIndex]?.textContent?.trim() || serviceEl.value);

    if (!activeVehicle) {
      setStatus("error", "Select a vehicle first.");
      return;
    }

    const serviceCode = editingLineItem ? editingLineItem.serviceCode : serviceEl.value;
    const serviceText = cleanCustomerFacingServiceLabel(serviceCode, rawServiceText, activeVehicle);

    const pricingSnapshot = buildPricingSnapshotFromControls();
    const it = {
      id: createLineItemId(),
      vehicleId: activeVehicle.id,
      vehicleLabel: getVehicleLabel(activeVehicle),
      vehicleYear: activeVehicle.year || "",
      vehicleMake: activeVehicle.make || "",
      vehicleModel: activeVehicle.model || "",
      vehicleDisplayModel: getVehicleDisplayModel(activeVehicle),
      serviceCode,
      serviceText,
      status: "recommended",
      ...pricingSnapshot,
      notes: (notesEl?.value || "").trim() || null,
      inspectionFindings: "",
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
        scrollLineItemIntoView(it.id);
        trackClarity("estimate_generated", {
          source: "estimator",
          action: "get_estimate",
          service_code: it.serviceCode,
          service_name: it.serviceText,
          estimate_total: Number(it.estimate || 0)
        });
        setStatus("ok", `${it.serviceText}: ${money(it.estimate)} added. Add another repair or create the customer quote.`);

        readyForNextService = false;
        isAddingLineItem = false;
        updateEstimateButtonState();
        focusAddAnotherRepair();
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
      scrollLineItemIntoView(it.id);
      trackClarity("estimate_generated", {
        source: "estimator",
        action: "get_estimate",
        service_code: it.serviceCode,
        service_name: it.serviceText,
        estimate_total: Number(it.estimate || 0)
      });
      setStatus("ok", `${it.serviceText}: ${money(it.estimate)} added. Add another repair or create the customer quote.`);

      readyForNextService = false;
      isAddingLineItem = false;
      updateEstimateButtonState();
      focusAddAnotherRepair();
      void refreshPairedSuggestions();
    } catch (e) {
      const currentIndex = lineItems.indexOf(it);
      if (currentIndex >= 0) {
        lineItems.splice(currentIndex, 1);
      }
      renderLineItems();
      void refreshPairedSuggestions();

      readyForNextService = true;
      isAddingLineItem = false;
      updateEstimateButtonState();

      setStatus("error", `Estimate failed: ${e.message}`);
    }
  });

  // Add Another Service
  addLineBtn?.addEventListener("click", () => {
    if (isAddingLineItem || isGeneratingAllLines) return;

    // Clear selections
    activeEditingLineId = null;
    setCategoryValue("", "none");
    serviceEl.value = "";
    resetServiceSearch();
    serviceEl.innerHTML = `<option value="">Select service…</option>`;
    document.querySelectorAll(".tm-quick-quote").forEach((btn) => btn.classList.remove("is-selected"));
    hidePairedSuggestions();
    hideEstimatorWorkflowShortcuts();

    serviceMeta = null;
    if (laborHoursRangeEl) laborHoursRangeEl.textContent = "";

    laborHoursTouched = false;
    laborHoursEl.value = "0";
    partsPriceEl.value = "0";
    if (notesEl) notesEl.value = "";

    if (pricingModeEl) pricingModeEl.value = "hourly";
    if (laborRateEl) laborRateEl.value = String(getPreferredLaborRate());
    if (flatRatePriceEl) flatRatePriceEl.value = "0";
    if (travelFeeEl) travelFeeEl.value = String(getPreferredTravelFee());
    togglePricingModeUI();

    // Unlock Add Service to Estimate
    readyForNextService = true;
    updateEstimateButtonState();
    focusServiceAddArea();

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
      if (!lineItems.length) {
        readyForNextService = true;
        editingLineItem = null;
      }
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
      renderLineItems();
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
          scrollLineItemIntoView(it.id);
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
        scrollLineItemIntoView(it.id);
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

  lineItemsList?.addEventListener("input", (e) => {
    const input = e.target?.closest?.('[data-action="inspection-findings"]');
    if (!input) return;

    const lineItemId = input.dataset.lineItemId || input.closest(".tm-service-card")?.dataset?.lineItemId || "";
    const it = getLineItemById(lineItemId);
    if (!it) return;

    it.inspectionFindings = String(input.value || "").trim();
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
    syncLineItemsToVehicle();
  });

  lineItemsList?.addEventListener("change", (e) => {
    const input = e.target?.closest?.('[data-action="repair-status"]');
    if (!input) return;

    const lineItemId = input.dataset.lineItemId || input.closest(".tm-service-card")?.dataset?.lineItemId || "";
    const it = getLineItemById(lineItemId);
    if (!it) return;

    it.status = normalizeRepairStatus(input.value);
    syncLineItemsToVehicle();
    renderLineItems();
  });

  let isGeneratingCustomerPdf = false;

  // Confirm Add = finalize signature and generate PDF
  async function handleGenerateCustomerPdf(e) {
    e?.preventDefault();

    if (isGeneratingCustomerPdf) return;
    isGeneratingCustomerPdf = true;
    if (confirmAddBtn) confirmAddBtn.disabled = true;

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
            refreshApprovalStatus();
            setConfirmMessage("error", "Signature is selected, but the signature box is empty. Ask the customer to sign, or choose the no-signature PDF option.");
            return;
          }

          // Export signature directly from the live canvas with transparent background
          signatureDataUrl = sigCanvas.toDataURL("image/png");
          refreshApprovalStatus();
        } catch (err) {
          console.warn("Signature validation failed", err);
          setConfirmMessage("error", "Signature could not be read. Please clear the box and have the customer sign again.");
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
      const pdfLineItems = lineItems.map((it) => ({
        serviceCode: it.serviceCode,
        serviceText: it.serviceText,
        pricingMode: it.pricingMode,
        flatRatePrice: Number(it.flatRatePrice || 0),
        laborHours: Number(it.laborHours || 0),
        partsPrice: Number(it.partsPrice || 0),
        laborRate: Number(it.laborRate || 0),
        travelFee: Number(it.travelFee || 0),
        estimate: it.estimate != null ? Number(it.estimate) : null,
        status: normalizeRepairStatus(it.status),
        laborBreakdown: it.laborBreakdown || null,
        inspectionFindings: String(it.inspectionFindings || "").trim(),
      }));
      
      // Generate PDF
      setStatus("info", "Preparing customer PDF...");

      const activeVehicle = getActiveVehicle() || {};
      const outputOptions = getCustomerOutputOptions();
      const businessIdentity = getBusinessIdentity();
      persistMechanicPreferencesFromControls();

      const pdfResponse = await fetch("/estimate/pdf_multi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          year: Number(activeVehicle.year || 0),
          make: String(activeVehicle.make || "").trim(),
          model: String(activeVehicle.model || "").trim(),
          displayModel: getVehicleDisplayModel(activeVehicle),
          notes: (notesEl?.value || "").trim() || null,
          customerName: (customerNameEl?.value || "").trim() || null,
          customerPhone: (customerPhoneEl?.value || "").trim() || null,
          businessName: businessIdentity.businessName || null,
          mechanicName: businessIdentity.mechanicName || null,
          businessPhone: businessIdentity.businessPhone || null,
          businessNote: businessIdentity.businessNote || null,
          customerAgrees: !!customerAgreesChk?.checked || !!signatureDataUrl,
          signatureDataUrl,
          showGeneratedDate: outputOptions.showGeneratedDate,
          showHourlyRate: outputOptions.showHourlyRate,
          showLaborColumn: outputOptions.showLaborColumn,
          showPartsColumn: outputOptions.showPartsColumn,
          showRiskNotes: outputOptions.showRiskNotes,
          showInspectionFindings: outputOptions.showInspectionFindings,
          showDetailedLaborBreakdown: outputOptions.showDetailedLaborBreakdown,
          lineItems: pdfLineItems,
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

      // Use one controlled launch action. Mobile Safari often treats blob
      // downloads as opens, so avoid pairing this with window.open().
      const a = document.createElement("a");
      a.href = pdfUrl;
      a.download = "torquemech_estimate.pdf";
      a.target = "_blank";
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();

      setStatus("ok", "Customer PDF ready.");
      const finalizedDraft = saveCurrentDraft({ quiet: true });
      if (!finalizedDraft) {
        hideEstimateSavedBlock();
      }
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
        `;
      }

      // Do NOT auto-close immediately
      setTimeout(() => URL.revokeObjectURL(pdfUrl), 60000);

      setStatus("ok", "Customer PDF ready.");
      closeConfirm(true);

    } catch (e) {
      console.error("PDF generation failed", e);
      setStatus("error", "Unable to generate PDF. Please try again.");
      setConfirmMessage("error", "Unable to generate PDF. Please try again.");
    } finally {
      isGeneratingCustomerPdf = false;
      if (confirmAddBtn) confirmAddBtn.disabled = false;
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

  completionSuggestionsList?.addEventListener("click", async (e) => {
    const btn = e.target?.closest?.(".tm-completion-suggestion[data-service-code]");
    if (!btn) return;

    btn.disabled = true;
    try {
      await selectPairedSuggestion(btn.dataset.serviceCode || "", btn.dataset.serviceCategory || "");
    } catch (err) {
      console.warn("Quote completion suggestion failed", err);
      setStatus("error", "Unable to stage that related job. Choose it from the service list instead.");
    } finally {
      btn.disabled = false;
    }
  });

  estimatorWorkflowShortcutList?.addEventListener("click", async (e) => {
    const btn = e.target?.closest?.(".tm-estimator-workflow-shortcut[data-service-code]");
    if (!btn) return;

    btn.disabled = true;
    try {
      await selectPairedSuggestion(btn.dataset.serviceCode || "", btn.dataset.serviceCategory || "");
    } catch (err) {
      console.warn("Workflow shortcut selection failed", err);
      setStatus("error", "Unable to stage that workflow item. Choose it from the service list instead.");
    } finally {
      btn.disabled = false;
    }
  });

  document.querySelector(".tm-quick-quotes")?.addEventListener("click", async (e) => {
    const btn = e.target?.closest?.(".tm-quick-quote[data-quick-quote]");
    if (!btn) return;

    btn.disabled = true;
    try {
      await selectQuickQuoteShortcut(btn.dataset.quickQuote || "", {
        addToQuote: btn.dataset.quickAdd === "true",
      });
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
          displayModel: "",
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
    if (categoryEl) setCategoryValue("", "none");
    if (serviceEl) serviceEl.innerHTML = `<option value="">Select service…</option>`;
    resetServiceSearch();
    document.querySelectorAll(".tm-quick-quote").forEach((btn) => btn.classList.remove("is-selected"));

    // inputs
    if (laborHoursEl) laborHoursEl.value = "0";
    if (partsPriceEl) partsPriceEl.value = "0";
    if (laborRateEl) laborRateEl.value = String(getPreferredLaborRate());
    if (pricingModeEl) pricingModeEl.value = "hourly";
    if (flatRatePriceEl) flatRatePriceEl.value = "0";
    if (travelFeeEl) travelFeeEl.value = String(getPreferredTravelFee());
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
    activeDraftId = "";
    hideEstimateSavedBlock();
    try {
      localStorage.removeItem(LAST_DRAFT_ID_KEY);
    } catch (_) {}
    const confirmServicesList = document.getElementById("confirmServicesList");
    if (confirmServicesList) confirmServicesList.textContent = "—";

    // UI blocks
    if (lineItemsList) lineItemsList.innerHTML = "";
    lineItemsWrap?.classList.add("hidden");
    hidePairedSuggestions();
    hideQuoteCompletionSuggestions();

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
    refreshActiveVehicleControls();
    const vin = normalizeVinInput(vinEl?.value || "");
    if (vinEl) vinEl.value = vin;
    if (vinDecodedMeta) vinDecodedMeta.textContent = "";

    if (vin.length !== 17) {
      setStatus("error", "VIN must be 17 characters.");
      return;
    }

    if (!yearEl || !makeEl || !modelEl) {
      await renderVehicles();
      refreshActiveVehicleControls();
    }

    if (!yearEl || !makeEl || !modelEl) {
      setStatus("error", "Select Vehicle first, then fill vehicle details from VIN.");
      return;
    }

    setStatus("info", "Decoding VIN…");

    try {
      const res = await apiJSON(`/api/vin/${encodeURIComponent(vin)}`);

      const makeIndex = findVehicleOptionIndex(makeEl, res.make);
      if (makeIndex < 0) {
        setStatus("warn", `VIN decoded, but make "${res.make}" not found.`);
        return;
      }
      const activeVehicle = estimateState.vehicles.find(v => v.id === estimateState.activeVehicleId) || estimateState.vehicles[0];
      const canonicalMake = makeEl.options[makeIndex]?.value || res.make || "";
      const canonicalModel = String(res.model || "").trim();
      if (activeVehicle) {
        activeVehicle.year = String(res.year || "");
        activeVehicle.make = canonicalMake;
        activeVehicle.model = canonicalModel;
        activeVehicle.displayModel = canonicalModel;
        window.estimateState = estimateState;
      }

      await renderVehicles();
      refreshActiveVehicleControls();

      const modelIndex = findVehicleOptionIndex(modelEl, canonicalModel);
      if (modelIndex < 0 || !modelEl.value) {
        updateEstimateButtonState();
        setStatus("error", `VIN decoded, but model "${canonicalModel}" is not available for this year and make.`);
        return;
      }
      if (vinDecodedMeta) {
        vinDecodedMeta.textContent = `Detected: ${res.year} ${res.make} ${canonicalModel}`;
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
      categorySelectionSource = categoryEl.value ? "manual" : "none";
      updateCategoryClearButton();
      await loadServices(categoryEl.value);
      updateEstimateButtonState();
    } catch (e) {
      setStatus("error", `Services failed: ${e.message}`);
    }
  });

  categoryClearBtn?.addEventListener("click", async () => {
    setCategoryValue("", "none");
    await loadServices("");
    updateEstimateButtonState();
    categoryEl?.focus();
  });

  serviceEl?.addEventListener("change", async () => {
    try {
      syncServiceSearchFromSelect();
      await loadServiceMeta(serviceEl.value);
      updateEstimateButtonState();
      void refreshPairedSuggestions();
    } catch (e) {
      setStatus("error", `Service detail failed: ${e.message}`);
    }
  });

  serviceSearch?.addEventListener("input", async () => {
    if (!serviceEl) return;

    const searchValue = serviceSearch.value.trim();
    updateServiceClearButton();

    clearTimeout(searchDebounceTimer);

    if (searchValue.length >= 2) {
      searchDebounceTimer = setTimeout(() => {
        trackClarity("search_submit", { query: searchValue });
      }, 500); // 500ms delay
    }

    serviceEl.value = "";
    hidePairedSuggestions();

    if (categoryEl?.value && categorySelectionSource !== "manual") {
      setCategoryValue("", "none");
      serviceOptions = [];
    }

    if (!hasManualCategoryFilter()) {
      await ensureAllServiceOptions(serviceSearch.value);
    }

    renderServiceResults(serviceSearch.value);
    await loadServiceMeta("");
    updateServiceClearButton();
    updateEstimateButtonState();
  });

  serviceSearch?.addEventListener("focus", async () => {
    if (serviceSearch.value.trim()) {
      if (!hasManualCategoryFilter()) {
        await ensureAllServiceOptions(serviceSearch.value);
      }
      renderServiceResults(serviceSearch.value);
    }
  });

  serviceSearch?.addEventListener("blur", () => {
    setTimeout(hideServiceResults, 150);
  });

  serviceClearBtn?.addEventListener("click", async () => {
    await clearServiceSelection({ focus: true });
  });

  serviceResults?.addEventListener("click", async (event) => {
    const resultButton = event.target.closest(".service-result-item");
    if (!resultButton) return;
    const serviceCode = resultButton.dataset.serviceCode || "";
    const serviceCategory = resultButton.dataset.serviceCategory || "";
    const categorySource = hasManualCategoryFilter() ? "manual" : "auto";
    if (serviceCategory && categoryEl && categoryEl.value !== serviceCategory) {
      setCategoryValue(serviceCategory, categorySource);
      await loadServices(serviceCategory);
    } else if (serviceCategory && categoryEl) {
      setCategoryValue(serviceCategory, categorySource);
    }
    applyServiceSelection(serviceCode);
    trackClarity("service_selected", {
      service_code: serviceCode,
      category: serviceCategory || categoryEl?.value || "",
      service_name: serviceEl?.options?.[serviceEl.selectedIndex]?.textContent?.trim() || ""
    });
    await loadServiceMeta(serviceEl.value);
    updateEstimateButtonState();
    void refreshPairedSuggestions();
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
    v.displayModel = modelEl?.options?.[modelEl.selectedIndex]?.textContent?.trim() || v.model;

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

    if (openConfirm()) {
      setConfirmMessage("info", "Review the saved quote, then generate the customer PDF.");
    }
  });
  sharedDownloadPdfBtn?.addEventListener("click", () => {
    if (!lineItems.length) {
      setStatus("error", "Load or build an estimate before downloading a PDF.");
      return;
    }

    if (openConfirm()) {
      setConfirmMessage("info", "Review the shared quote, then generate the customer PDF.");
    }
  });
  convertToProJobBtn?.addEventListener("click", () => {
    if (!lineItems.length) {
      setStatus("error", "Add at least one service before converting to a Pro job.");
      return;
    }
    if (!convertToProJobForm || !convertToProJobPayload) return;
    convertToProJobPayload.value = JSON.stringify(buildProJobConversionPayload());
    convertToProJobForm.submit();
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
  [businessNameEl, mechanicNameEl, businessPhoneEl, businessNoteEl].forEach((el) => {
    el?.addEventListener("input", () => {
      persistMechanicPreferencesFromControls();
      refreshQuoteIdentityNudge();
    });
  });

  [laborRateEl, travelFeeEl].forEach((el) => {
    el?.addEventListener("input", persistMechanicPreferencesFromControls);
    el?.addEventListener("blur", persistMechanicPreferencesFromControls);
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
    if (!lineItems.length) {
      activeEditingLineId = null;
      editingLineItem = null;
      readyForNextService = true;
    }

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
          cursor:default;
        "
      >
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px;">
          <h3 style="margin:0;">${estimateState.vehicles.length > 1 ? `Vehicle ${idx + 1}` : "Vehicle"}</h3>
          ${estimateState.vehicles.length > 1 && idx > 0 ? `
            <button type="button" class="ghost remove-vehicle-btn" data-vehicle-id="${vehicle.id}">
              Remove Vehicle
            </button>
          ` : ""}
        </div>

        <div class="grid3">
          <div class="tm-year-field">
            <label>Year</label>
            <select class="vehicle-year" data-vehicle-id="${vehicle.id}"></select>
          </div>

          <div class="tm-inline-clear-field">
            <label>Make</label>

            <input
              type="text"
              class="vehicle-make-search"
              data-vehicle-id="${vehicle.id}"
              placeholder="Search make..."
              autocomplete="off"
            />
            <button type="button" class="tm-input-clear-btn vehicle-make-clear" data-vehicle-id="${vehicle.id}" aria-label="Clear make" hidden>
              &times;
            </button>

            <div
              class="vehicle-make-results"
              data-vehicle-id="${vehicle.id}"
              style="display:none; margin-top:6px; border:1px solid rgba(255,255,255,.12); border-radius:12px; overflow-y:auto;"
            ></div>

            <select class="vehicle-make" data-vehicle-id="${vehicle.id}" style="display:none;">
              <option value="">Select make...</option>
            </select>
          </div>

          <div class="tm-inline-clear-field">
            <label>Model</label>
            <select class="vehicle-model" data-vehicle-id="${vehicle.id}">
              <option value="">Select model...</option>
            </select>
            <button type="button" class="tm-input-clear-btn vehicle-model-clear" data-vehicle-id="${vehicle.id}" aria-label="Clear model" hidden>
              &times;
            </button>
          </div>
        </div>
      </div>
    `).join("");

    await bindVehicleCardFields();
    refreshActiveVehicleControls();
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
          displayModel: vehicle.displayModel || vehicle.model || "",
        },
        onChange: ({ year, make, model, displayModel }) => {
          vehicle.year = year;
          vehicle.make = make;
          vehicle.model = model;
          vehicle.displayModel = displayModel || model;
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
    if (isGeneratingAllLines) return;

    try {
      if (hasOpenLineEdit()) {
        setStatus("error", "Save the current line edit before creating the customer quote.");
        focusOpenLineEdit();
        return;
      }

      if (!lineItems.length) {
        setStatus("error", "Add at least one service first.");
        return;
      }

      isGeneratingAllLines = true;
      if (generateAllBtn) generateAllBtn.disabled = true;

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
      isGeneratingAllLines = false;
      if (generateAllBtn) generateAllBtn.disabled = false;
      openConfirm();

    } catch (e) {
      setStatus("error", `Generate all failed: ${e.message}`);
    } finally {
      isGeneratingAllLines = false;
      if (generateAllBtn) generateAllBtn.disabled = false;
      updateEstimateButtonState();
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
      setStatus("info", "Estimator ready.");
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
    const displayModel = params.get("displayModel") || params.get("display_model");
    const service = params.get("service");
    const source = params.get("source") || "";
    const findingContext = getEstimatorSourceContext();
    const handoffTrustEl = $("repairGuideHandoffTrust");

    if (!year && !make && !model && !displayModel && !service && findingContext.source !== "finding") return;

    function updateRepairGuideHandoffTrust({ vehicleLoaded = false, serviceLoaded = false } = {}) {
      if (!handoffTrustEl) return;
      const cameFromGuide = source === "repair-guide" || Boolean(service);
      if (!cameFromGuide) return;

      const details = vehicleLoaded && serviceLoaded
        ? "Vehicle and repair path carried into this quote."
        : serviceLoaded
          ? "Repair path carried into pricing."
          : "Guide context carried into the estimator.";

      handoffTrustEl.innerHTML = `
        <div>
          <strong>Workflow context loaded</strong>
          <span>${details}</span>
        </div>
        <em>Next: review pricing.</em>
      `;
      handoffTrustEl.classList.remove("hidden");
    }

    async function preloadVehicle() {
      const activeVehicle = getActiveVehicle();
      if (!activeVehicle) return false;

      if (year) activeVehicle.year = year;
      if (make) activeVehicle.make = make;
      if (model) {
        activeVehicle.model = model;
        activeVehicle.displayModel = displayModel || model;
      } else if (displayModel) {
        activeVehicle.model = displayModel;
        activeVehicle.displayModel = displayModel;
      }

      window.estimateState = estimateState;
      await renderVehicles();
      renderActiveVehicleBanner();
      updateEstimateButtonState();
      return Boolean(year || make || model || displayModel);
    }

    function renderFindingEstimateContext() {
      if (findingContext.source !== "finding") return;

      if (customerNameEl && findingContext.customerName) {
        customerNameEl.value = findingContext.customerName;
      }

      const vehicleText = [year, make, displayModel || model].filter(Boolean).join(" ");
      const findingText = findingContext.problemFound || findingContext.recommendedRepair || "";
      if (findingEstimateContextText) {
        const lines = [
          findingContext.customerName ? `Customer: ${findingContext.customerName}` : "",
          vehicleText ? `Vehicle: ${vehicleText}` : "",
          findingText ? `Finding: ${findingText}` : "",
        ].filter(Boolean);
        findingEstimateContextText.textContent = lines.join(" | ");
      }
      findingEstimateContext?.classList.remove("hidden");

      if (findingContext.recommendedRepair && notesEl && !notesEl.value.trim()) {
        notesEl.value = `Recommended Repair: ${findingContext.recommendedRepair}`;
      }
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

        setCategoryValue(categoryKey, "auto");
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
        const vehicleLoaded = await preloadVehicle();
        let serviceLoaded = false;

        if (service) {
          const found = await preloadServiceByCode(service);
          serviceLoaded = found;
          if (!found) {
            console.warn("Could not auto-find service:", service);
          }
        }

        updateRepairGuideHandoffTrust({ vehicleLoaded, serviceLoaded });
        renderFindingEstimateContext();
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
