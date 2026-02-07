console.log("app.js loaded ✅");

window.addEventListener("error", (e) => {
  console.error("JS Error:", e.message, e.filename, e.lineno);
});

function el(id) {
  return document.getElementById(id);
}

function setSelectOptions(selectEl, values, placeholder = "Select...") {
  if (!selectEl) return;
  selectEl.innerHTML = "";

  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = placeholder;
  ph.disabled = true;
  ph.selected = true;
  selectEl.appendChild(ph);

  values.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    selectEl.appendChild(opt);
  });
}

function fillDatalist(datalistEl, rows, getLabel) {
  if (!datalistEl) return;
  datalistEl.innerHTML = "";
  rows.forEach((row) => {
    const opt = document.createElement("option");
    opt.value = getLabel(row);
    datalistEl.appendChild(opt);
  });
}

async function getJSON(url) {
  const r = await fetch(url);
  const text = await r.text();
  console.log("GET", url, r.status, text.slice(0, 140));
  if (!r.ok) throw new Error(`${r.status}: ${text}`);
  return JSON.parse(text);
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await r.text();
  console.log("POST", url, r.status, text.slice(0, 160));
  if (!r.ok) throw new Error(`${r.status}: ${text}`);
  return JSON.parse(text);
}

// ---------------- ZIP datalist (Orange County) ----------------
const OC_ZIPS = [
  "90620","90621","90630","90631","90632","90680","90720",
  "92602","92603","92604","92606","92612","92614","92617","92618","92620",
  "92624","92625","92626","92627","92629","92630","92637",
  "92646","92647","92648","92649","92651","92653","92656","92657",
  "92660","92661","92662","92663",
  "92672","92673","92675","92677","92679","92688","92691","92692",
  "92701","92703","92704","92705","92706","92707","92708",
  "92801","92802","92804","92805","92806","92807","92808",
  "92821","92823","92831","92832","92833","92835",
  "92840","92841","92843","92844","92845",
  "92865","92866","92867","92869","92870","92861","92886","92887","92683","92684"
];

function loadZipDatalist() {
  const list = el("zipList");
  if (!list) return;
  const zips = [...new Set(OC_ZIPS)].sort();
  list.innerHTML = "";
  zips.forEach((z) => {
    const opt = document.createElement("option");
    opt.value = z;
    list.appendChild(opt);
  });
}

// ---------------- Vehicle (Year/Make/Model) ----------------
async function loadYears(yearEl) {
  const years = await getJSON("/vehicle/years");
  setSelectOptions(yearEl, years.map(String), "Select a year");
  // set a default (optional)
  if (years.length) yearEl.value = String(years[0]);
}

async function loadMakes(makeEl, yearVal) {
  const makes = await getJSON(`/vehicle/makes?year=${encodeURIComponent(yearVal)}`);
  setSelectOptions(makeEl, makes, "Select a make");
}

async function loadModels(modelEl, yearVal, makeVal) {
  if (!makeVal) {
    setSelectOptions(modelEl, [], "Select a model");
    return;
  }
  const models = await getJSON(`/vehicle/models?year=${encodeURIComponent(yearVal)}&make=${encodeURIComponent(makeVal)}`);
  setSelectOptions(modelEl, models, "Select a model");
}

// ---------------- Catalog (Category/Service) ----------------
let categoriesCache = []; // [{key,name,count}]
let servicesCacheByCategory = new Map(); // key -> services[]

function keyByCategoryName(name) {
  const n = (name || "").trim().toLowerCase();
  const found = categoriesCache.find((c) => (c.name || "").trim().toLowerCase() === n);
  return found ? found.key : "";
}

function codeByServiceName(services, name) {
  const n = (name || "").trim().toLowerCase();
  const found = services.find((s) => (s.name || "").trim().toLowerCase() === n);
  return found ? found.code : "";
}

async function loadCategories() {
  categoriesCache = await getJSON("/categories");
  fillDatalist(el("categoryList"), categoriesCache, (c) => c.name);
}

async function loadServicesForCategory(categoryKey) {
  if (!categoryKey) return [];
  if (servicesCacheByCategory.has(categoryKey)) return servicesCacheByCategory.get(categoryKey);

  const services = await getJSON(`/services/${encodeURIComponent(categoryKey)}`);
  servicesCacheByCategory.set(categoryKey, services);
  return services;
}

async function hydrateServicesUI(categoryKey) {
  const services = await loadServicesForCategory(categoryKey);
  fillDatalist(el("serviceList"), services, (s) => s.name);
}

// --- Searchable dropdowns (Choices.js) ---
const _choices = {}; // store instances by element id

function enhanceSelect(selectEl, placeholder = "Select...") {
  if (!selectEl) return;

  // Only for <select>
  if (selectEl.tagName !== "SELECT") return;

  // If library didn't load, silently skip (dropdown still works normally)
  if (typeof Choices === "undefined") return;

  const id = selectEl.id || selectEl.name;
  if (!id) return;

  // Destroy previous instance (important when you re-populate options)
  if (_choices[id]) {
    _choices[id].destroy();
    delete _choices[id];
  }

  _choices[id] = new Choices(selectEl, {
    searchEnabled: true,
    shouldSort: false,              // keep your API order
    placeholder: true,
    placeholderValue: placeholder,
    searchPlaceholderValue: "Type to search...",
    itemSelectText: "",
    removeItemButton: false,
  });
}

// ---------------- Main init ----------------
document.addEventListener("DOMContentLoaded", async () => {
  const zipEl = el("zip");
  const partsPriceEl = el("partsPrice");
  const laborPricingEl = el("laborPricing");
  const vehicleTypeEl = el("vehicleType");

  const yearEl = el("year");
  const makeEl = el("make");
  const modelEl = el("model");

  const categorySearchEl = el("categorySearch");
  const categoryEl = el("category"); // hidden
  const serviceSearchEl = el("serviceSearch");
  const serviceEl = el("service");   // hidden

  const btn = el("estimateBtn") || el("go");
  const resultEl = el("result");

  const missing = [];
  if (!zipEl) missing.push("zip");
  if (!partsPriceEl) missing.push("partsPrice");
  if (!laborPricingEl) missing.push("laborPricing");
  if (!vehicleTypeEl) missing.push("vehicleType");
  if (!yearEl) missing.push("year");
  if (!makeEl) missing.push("make");
  if (!modelEl) missing.push("model");
  if (!categorySearchEl) missing.push("categorySearch");
  if (!categoryEl) missing.push("category(hidden)");
  if (!serviceSearchEl) missing.push("serviceSearch");
  if (!serviceEl) missing.push("service(hidden)");
  if (!btn) missing.push("estimateBtn");
  if (!resultEl) missing.push("result");

  if (missing.length) {
    console.error("Missing elements:", missing);
    return;
  }

  try {
    // ZIP autofill list
    loadZipDatalist();
    if (!zipEl.value) zipEl.value = "92646"; // optional default

    // Load vehicle
    await loadYears(yearEl);
    await loadMakes(makeEl, yearEl.value);
    await loadModels(modelEl, yearEl.value, makeEl.value);

    // Load catalog
    await loadCategories();

    console.log("All dropdowns ready ✅");

    // EVENTS
    yearEl.addEventListener("change", async () => {
      try {
        await loadMakes(makeEl, yearEl.value);
        await loadModels(modelEl, yearEl.value, makeEl.value);
      } catch (e) {
        console.error(e);
      }
    });

    makeEl.addEventListener("change", async () => {
      try {
        await loadModels(modelEl, yearEl.value, makeEl.value);
      } catch (e) {
        console.error(e);
      }
    });

    categorySearchEl.addEventListener("input", async () => {
      try {
        const key = keyByCategoryName(categorySearchEl.value);
        categoryEl.value = key;
        serviceEl.value = "";
        serviceSearchEl.value = "";
        await hydrateServicesUI(key);
      } catch (e) {
        resultEl.textContent = `Category change error: ${e.message}`;
      }
    });

    serviceSearchEl.addEventListener("input", async () => {
      try {
        const categoryKey = categoryEl.value;
        if (!categoryKey) return;

        const services = await loadServicesForCategory(categoryKey);
        const code = codeByServiceName(services, serviceSearchEl.value);
        serviceEl.value = code;
      } catch (e) {
        resultEl.textContent = `Service change error: ${e.message}`;
      }
    });

    btn.addEventListener("click", async () => {
      try {
        resultEl.textContent = "Calculating…";

        // ensure hidden values are set (in case user typed and didn't blur)
        const catKey = categoryEl.value || keyByCategoryName(categorySearchEl.value);
        categoryEl.value = catKey;

        const services = catKey ? await loadServicesForCategory(catKey) : [];
        const svcCode = serviceEl.value || codeByServiceName(services, serviceSearchEl.value);
        serviceEl.value = svcCode;

        if (!catKey) throw new Error("Pick a Category");
        if (!svcCode) throw new Error("Pick a Service");

        const payload = {
          zip_code: (zipEl.value || "").trim(),
          parts_price: Number(partsPriceEl.value || 0),
          labor_pricing: laborPricingEl.value,
          vehicle_type: vehicleTypeEl.value,

          year: Number(yearEl.value || 0) || null,
          make: makeEl.value || null,
          model: modelEl.value || null,

          category: catKey,
          service: svcCode,
        };

        const data = await postJSON("/estimate", payload);

        resultEl.innerHTML = `
          <div class="resultTitle">${data.service_name || "Estimate"}</div>
          <div class="resultMoney">$${data.estimate_low} – $${data.estimate_high}</div>

          <div class="resultMeta">
            Labor rate: $${data.labor_rate}/hr •
            Multiplier: ${data.vehicle_multiplier} (${data.vehicle_type}) •
            Hours: ${data.labor_hours_min}–${data.labor_hours_max}
          </div>

          <div class="resultMeta">
            Flat range: $${data.flat_rate_min}–$${data.flat_rate_max} •
            Parts used: $${data.parts_price_used} •
            Mode: ${data.labor_pricing}
          </div>
        `;
      } catch (e) {
        resultEl.textContent = `Error: /estimate failed: ${e.message}`;
      }
    });

  } catch (e) {
    console.error("Init failed:", e);
    resultEl.textContent = `Init failed: ${e.message}`;
  }
});
