// static/obd.js

document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("obdCode");
  const suggestBox = document.getElementById("suggestBox");
  const suggestList = document.getElementById("suggestList");
  const lookupBtn = document.getElementById("lookupBtn");
  const clearBtn = document.getElementById("clearBtn");
  const status = document.getElementById("status");
  const resultCard = document.getElementById("obdResultCard");
  const codeTitle = document.getElementById("obdCodeTitle");
  const codeSubtitle = document.getElementById("obdCodeSubtitle");
  const causesList = document.getElementById("obdCauses");
  const checksList = document.getElementById("obdChecks");
  const descEl = document.getElementById("obdDescription");
  const likelyWrap = document.getElementById("obdLikelyFixes");
  const likelyList = document.getElementById("obdLikelyFixesList");

  const repairCostsWrap = document.getElementById("obdRepairCosts");
  const repairCostsList = document.getElementById("obdRepairCostsList");
  
  let lastLookedUpCode = "";

  let selectedCodes = [];

  const addToSelectionBtn = document.getElementById("addToSelectionBtn");
  const sendSelectedBtn = document.getElementById("sendSelectedBtn");
  const selectedTray = document.getElementById("selectedCodesTray");
  const selectedList = document.getElementById("selectedCodesList");
  const clearSelectedBtn = document.getElementById("clearSelectedBtn");

  const relatedWrap = document.getElementById("obdRelated");
  const relatedList = document.getElementById("obdRelatedList");

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function buildRelatedCodes(codeUpper) {
    const code = (codeUpper || "").toUpperCase().trim();
    const out = [];

    // P0300 → P0301–P0308 (cylinder misfires)
    if (code === "P0300") {
      for (let cyl = 1; cyl <= 8; cyl++) {
        const c = `P030${cyl}`;
        out.push({ code: c, label: `Cylinder ${cyl} Misfire` });
      }
      return out;
    }

    // Any P0301–P0308 → show P0300 + neighbors
    if (/^P030[1-8]$/.test(code)) {
      out.push({ code: "P0300", label: "Random/Multiple Cylinder Misfire" });
      const cyl = parseInt(code.slice(-1), 10);
      const prev = cyl - 1;
      const next = cyl + 1;
      if (prev >= 1) out.push({ code: `P030${prev}`, label: `Cylinder ${prev} Misfire` });
      if (next <= 8) out.push({ code: `P030${next}`, label: `Cylinder ${next} Misfire` });
      return out;
    }

    // Fuel trim lean/rich pairs
    if (code === "P0171") return [{ code: "P0174", label: "System Too Lean (Bank 2)" }];
    if (code === "P0174") return [{ code: "P0171", label: "System Too Lean (Bank 1)" }];
    if (code === "P0172") return [{ code: "P0175", label: "System Too Rich (Bank 2)" }];
    if (code === "P0175") return [{ code: "P0172", label: "System Too Rich (Bank 1)" }];

    // Catalyst efficiency pairs
    if (code === "P0420") return [{ code: "P0430", label: "Catalyst Efficiency Below Threshold (Bank 2)" }];
    if (code === "P0430") return [{ code: "P0420", label: "Catalyst Efficiency Below Threshold (Bank 1)" }];

    // EVAP small/large leaks (common cluster)
    if (code === "P0442") return [{ code: "P0455", label: "EVAP Large Leak Detected" }, { code: "P0456", label: "EVAP Very Small Leak Detected" }];
    if (code === "P0455") return [{ code: "P0442", label: "EVAP Small Leak Detected" }, { code: "P0456", label: "EVAP Very Small Leak Detected" }];
    if (code === "P0456") return [{ code: "P0442", label: "EVAP Small Leak Detected" }, { code: "P0455", label: "EVAP Large Leak Detected" }];

    return out;
  }

  function renderRelatedCodes(items) {
    if (!relatedWrap || !relatedList) return;

    relatedList.innerHTML = "";

    if (!items || items.length === 0) {
      relatedWrap.style.display = "none";
      return;
    }

    relatedWrap.style.display = "block";

    items.forEach(({ code, label }) => {
      const a = document.createElement("a");
      a.className = "rel-chip";
      a.href = `/obd?prefill=${code}`
      a.target = "_blank";
      a.rel = "noopener";

      const c = document.createElement("span");
      c.className = "rel-code";
      c.textContent = code;

      const t = document.createElement("span");
      t.className = "rel-text";
      t.textContent = label;

      a.appendChild(c);
      a.appendChild(t);
      relatedList.appendChild(a);
    });
  }

  function renderSelectedCodes() {
    if (!selectedList || !selectedTray || !sendSelectedBtn) return; // ✅ guard

    selectedList.innerHTML = "";

    if (selectedCodes.length === 0) {
      selectedTray.style.display = "none";
      sendSelectedBtn.disabled = true;
      return;
    }

    selectedTray.style.display = "block";
    sendSelectedBtn.disabled = false;

    selectedCodes.forEach(code => {
      const pill = document.createElement("div");
      pill.textContent = code;
      // … your pill styling + click remove logic
      selectedList.appendChild(pill);
    });
  }

  function setStatus(msg, type = "") {
    status.textContent = msg;
    status.style.color =
      type === "error" ? "#ff6b6b" :
      type === "success" ? "#4ade80" :
      "#94a3b8";
  }

  function normalize(code) {
    return (code || "")
      .toUpperCase()
      .replace(/[^A-Z0-9]/g, "")
      .slice(0, 7);
  }

  const OBD_SEVERITY_RULES = {
    exact: {
      P0300: "high",
      P0301: "high",
      P0302: "high",
      P0303: "high",
      P0304: "high",
      P0305: "high",
      P0306: "high",
      P0307: "high",
      P0308: "high",

      P0420: "medium",
      P0430: "medium",

      P0171: "medium",
      P0174: "medium",
      P0172: "medium",
      P0175: "medium",

      P0442: "low",
      P0455: "low",
      P0456: "low",
      P0446: "low"
    },

    prefix: {
      P03: "high",
      P04: "medium",
      P01: "medium",
      C: "low",
      B: "low",
      U: "medium"
    }
  };

  function getSeverityForCode(codeUpper) {
    const code = (codeUpper || "").toUpperCase().trim();

    if (OBD_SEVERITY_RULES.exact[code]) {
      return OBD_SEVERITY_RULES.exact[code];
    }

    const prefixes = Object.keys(OBD_SEVERITY_RULES.prefix)
      .sort((a, b) => b.length - a.length);

    for (const prefix of prefixes) {
      if (code.startsWith(prefix)) {
        return OBD_SEVERITY_RULES.prefix[prefix];
      }
    }

    return "medium";
  }

  function renderList(el, items) {
    el.innerHTML = "";
    items.forEach(text => {
      const li = document.createElement("li");
      li.textContent = text;
      el.appendChild(li);
    });
  }

let tmr = null;
let lastQ = "";

function showSuggest(show) {
  if (!suggestBox) return;
  suggestBox.style.display = show ? "block" : "none";
}

function renderSuggest(results) {
  if (!suggestList) return;
  
    // Dedupe + normalize codes (prevents p030 / P0300 duplicates)
  const seen = new Set();
  results = (results || [])
    .map(r => ({
      ...r,
      code: (r.code || "").toUpperCase()
    }))
    .filter(r => {
      if (!r.code) return false;
      if (seen.has(r.code)) return false;
      seen.add(r.code);
      return true;
    });

  suggestList.innerHTML = "";

  if (results.length === 0) {
    const empty = document.createElement("div");
    empty.style.opacity = "0.8";
    empty.style.fontSize = "13px";
    empty.textContent = "No matches.";
    suggestList.appendChild(empty);
    return;
  }

  results.forEach((r) => {
    const row = document.createElement("button");
    row.type = "button";
    row.style.display = "flex";
    row.style.width = "100%";
    row.style.gap = "10px";
    row.style.alignItems = "baseline";
    row.style.textAlign = "left";
    row.style.padding = "10px";
    row.style.borderRadius = "12px";
    row.style.border = "1px solid rgba(255,255,255,.08)";
    row.style.background = "rgba(255,255,255,.04)";
    row.style.cursor = "pointer";
    row.style.marginBottom = "8px";

    const code = document.createElement("div");
    code.style.fontWeight = "800";
    code.style.minWidth = "72px";
    code.textContent = r.code;

    const title = document.createElement("div");
    title.style.opacity = "0.9";
    title.style.fontSize = "13px";
    title.textContent = r.title || "";

    row.appendChild(code);
    row.appendChild(title);

    row.addEventListener("click", () => {
      input.value = normalize(r.code);
      showSuggest(false);
      lookup(); // run the normal lookup
    });

    suggestList.appendChild(row);
  });
}

async function runSuggest() {
  const q = normalize(input.value);

  // only show suggestions when they typed at least 2 chars (P0, P03, U01, etc.)
  if (!q || q.length < 2) {
    showSuggest(false);
    return;
  }

  // don't spam same query
  if (q === lastQ) return;
  lastQ = q;

  try {
    const res = await fetch(`/api/obd/search?q=${encodeURIComponent(q)}&limit=12`);
    const data = await res.json();
    const results = data?.results || [];
    renderSuggest(results);
    showSuggest(true);
  } catch {
    showSuggest(false);
  }
}

// debounce as user types
input.addEventListener("input", () => {
  clearTimeout(tmr);
  tmr = setTimeout(runSuggest, 180);
});

// hide suggestions when clicking outside
document.addEventListener("click", (e) => {
  if (!suggestBox) return;
  const isInside = suggestBox.contains(e.target) || input.contains(e.target);
  if (!isInside) showSuggest(false);
});

// ESC hides
input.addEventListener("keydown", (e) => {
  if (e.key === "Escape") showSuggest(false);
});


  async function lookup() {
    const code = normalize(input.value);

    if (!code) {
      setStatus("Enter a valid OBD code.", "error");
      return;
    }

    setStatus("Looking up...");
    resultCard.style.display = "none";

    try {
      const res = await fetch(`/api/obd/lookup?code=${code}`);
      const data = await res.json();

    if (res.status === 404) {
        setStatus("Code not found yet.", "error");

    if (likelyWrap) likelyWrap.style.display = "none";

        // Show card
        resultCard.style.display = "block";
        resultCard.classList.add("show");

        document.getElementById("obdDivider")?.classList.add("active");

        codeTitle.textContent = `${code} — Not found`;

        causesList.innerHTML = "";
        checksList.innerHTML = "";

        return;
        }

        if (!res.ok) {
        setStatus("Server error.", "error");
        return;
        }

        lastLookedUpCode = code;
        

    setStatus("Code found.", "success");

    // Show card
    resultCard.style.display = "block";
    resultCard.classList.add("show");

    document.getElementById("obdDivider")?.classList.add("active");

    // Main title
    codeTitle.textContent = data.code;
    codeSubtitle.textContent = data.title || "";
    if (descEl) descEl.textContent = data.description || "No description available.";

    // Link to full diagnostic guide
    const guide = document.getElementById("diagGuideLink");
    if (guide) {
      guide.href = `/obd/${data.code.toLowerCase()}`;
    }

    document.getElementById("obdDiagGuide").style.display = "block";

    // Show guide preview card
    const preview = document.getElementById("guidePreview");
    const previewTitle = document.getElementById("guidePreviewTitle");
    const previewLink = document.getElementById("guidePreviewLink");

    if (preview) preview.style.display = "block";

    if (previewTitle) {
      previewTitle.textContent = data.title || "";
    }

    if (previewLink) {
      previewLink.href = `/obd/${data.code.toLowerCase()}`;
    }

    // Severity (simple Beta rule for now)
    const severity = getSeverityForCode(data.code);

    const badge = document.getElementById("obdSeverityBadge");
    if (badge) {
    badge.textContent = severity.toUpperCase();
    badge.className = "badge badge-" + severity;
    }

    // Causes
    renderList(causesList, data.possible_causes || []);

    // Checks
    renderList(checksList, data.quick_checks || []);

    // --- Likely Fix Probabilities (Beta heuristic) ---
    function buildLikelyFixes(code, causes) {
      const c = (causes || []).map(x => (x || "").toLowerCase());

      // default: show top 3–4 causes equally if we can't classify
      let picks = [];

      const has = (kw) => c.some(v => v.includes(kw));

      if (code.startsWith("P03")) {
        // Misfire-ish
        if (has("coil")) picks.push(["Ignition coil", 55]);
        if (has("spark")) picks.push(["Spark plugs", 25]);
        if (has("vacuum")) picks.push(["Vacuum leak", 12]);
        if (has("inject")) picks.push(["Fuel injector", 8]);
      } else if (code.startsWith("P04")) {
        // EVAP-ish
        if (has("cap")) picks.push(["Gas cap / seal", 45]);
        if (has("purge")) picks.push(["Purge valve", 25]);
        if (has("leak")) picks.push(["EVAP leak (hoses)", 20]);
        if (has("vent")) picks.push(["Vent valve", 10]);
      } else if (code.startsWith("P01")) {
        // Fuel trim-ish
        if (has("vacuum")) picks.push(["Vacuum leak", 40]);
        if (has("maf")) picks.push(["MAF sensor / intake air", 30]);
        if (has("fuel")) picks.push(["Fuel pressure / delivery", 20]);
        picks.push(["O2 sensor / exhaust leak", 10]);
      }

      // fallback if nothing matched
      if (picks.length === 0) {
        const base = ["Most common cause", "Second most common", "Third most common"];
        const n = Math.min(3, Math.max(1, (causes || []).length));
        const pct = n === 1 ? [100] : n === 2 ? [60,40] : [50,30,20];
        picks = Array.from({length:n}, (_, i) => [ (causes[i] || base[i]), pct[i] ]);
      }

      return picks;
    }

    function renderLikelyFixes(items) {
      if (!likelyWrap || !likelyList) return;
      likelyList.innerHTML = "";

      if (!items || items.length === 0) {
        likelyWrap.style.display = "none";
        return;
      }

      likelyWrap.style.display = "block";

      items.forEach(([label, pct]) => {
        const row = document.createElement("div");
        row.className = "fix-row";

        const top = document.createElement("div");
        top.className = "fix-top";

        const l = document.createElement("div");
        l.className = "fix-label";
        l.textContent = label;

        const right = document.createElement("div");
        right.className = "fix-right";

        const p = document.createElement("div");
        p.className = "fix-pct";
        p.textContent = `${pct}%`;

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "tm-btn ghost fix-estimate-btn";
        btn.textContent = "Estimate Repair →";

        btn.addEventListener("click", () => {
          const codes = selectedCodes.length ? selectedCodes.join(",") : lastLookedUpCode;
          window.location.href = `/estimator?obd=${encodeURIComponent(codes)}`;
        });

        right.appendChild(p);
        right.appendChild(btn);

        top.appendChild(l);
        top.appendChild(right);

        const bar = document.createElement("div");
        bar.className = "fix-bar";

        const fill = document.createElement("span");
        bar.appendChild(fill);

        row.appendChild(top);
        row.appendChild(bar);
        likelyList.appendChild(row);

        requestAnimationFrame(() => {
          fill.style.width = `${pct}%`;
        });
      });
    }

    function buildRepairCosts(code, causes) {
      const c = (causes || []).map(x => (x || "").toLowerCase());
      const items = [];

      const has = (kw) => c.some(v => v.includes(kw));

      if (code.startsWith("P03")) {
        if (has("coil")) items.push({
          label: "Ignition coil replacement",
          range: "$180 – $420",
          difficulty: "Easy",
          labor: "0.5 – 1.0 hr"
        });

        if (has("spark")) items.push({
          label: "Spark plug replacement",
          range: "$120 – $350",
          difficulty: "Easy",
          labor: "0.8 – 1.5 hr"
        });

        if (has("inject")) items.push({
          label: "Fuel injector replacement",
          range: "$250 – $700",
          difficulty: "Medium",
          labor: "1.0 – 2.5 hr"
        });

        if (has("vacuum")) items.push({
          label: "Vacuum leak repair",
          range: "$90 – $250",
          difficulty: "Easy–Medium",
          labor: "0.5 – 1.5 hr"
        });
      }

      if (items.length === 0) return [];
      return items;
    }

    function renderRepairCosts(items) {
      if (!repairCostsWrap || !repairCostsList) return;

      repairCostsList.innerHTML = "";

      if (!items || items.length === 0) {
        repairCostsWrap.style.display = "none";
        return;
      }

      repairCostsWrap.style.display = "block";

      items.forEach((item) => {
        const row = document.createElement("a");
        row.className = "cost-row cost-row--link";
        row.href = `/estimator?service=${encodeURIComponent(item.label)}`;

        const left = document.createElement("div");

        const l = document.createElement("div");
        l.className = "cost-label";
        l.textContent = item.label;

        const meta = document.createElement("div");
        meta.className = "cost-meta";
        meta.textContent = `Difficulty: ${item.difficulty} · Labor: ${item.labor}`;

        left.appendChild(l);
        left.appendChild(meta);

        const r = document.createElement("div");
        r.className = "cost-range";
        r.textContent = item.range;

        row.appendChild(left);
        row.appendChild(r);

        repairCostsList.appendChild(row);
      });
    }
    // compute + render
    const likely = buildLikelyFixes(data.code, data.possible_causes || []);
    renderLikelyFixes(likely);

    const repairCosts = buildRepairCosts(data.code, data.possible_causes || []);
    renderRepairCosts(repairCosts);

    function buildClusterRelatedCodes(codeUpper) {

      const code = (codeUpper || "").toUpperCase().trim();

      const clusters = {

        misfire: [
          ["P0300","Random/Multiple Cylinder Misfire"],
          ["P0301","Cylinder 1 Misfire"],
          ["P0302","Cylinder 2 Misfire"],
          ["P0303","Cylinder 3 Misfire"],
          ["P0304","Cylinder 4 Misfire"],
          ["P0305","Cylinder 5 Misfire"],
          ["P0306","Cylinder 6 Misfire"],
          ["P0307","Cylinder 7 Misfire"],
          ["P0308","Cylinder 8 Misfire"]
        ],

        fuel_trim: [
          ["P0171","System Too Lean (Bank 1)"],
          ["P0174","System Too Lean (Bank 2)"],
          ["P0172","System Too Rich (Bank 1)"],
          ["P0175","System Too Rich (Bank 2)"],
          ["P0101","MAF Sensor Performance"]
        ],

        catalyst: [
          ["P0420","Catalyst Efficiency Below Threshold (Bank 1)"],
          ["P0430","Catalyst Efficiency Below Threshold (Bank 2)"],
          ["P0137","O2 Sensor Circuit Low Voltage"],
          ["P0141","O2 Sensor Heater Circuit"],
          ["P0157","O2 Sensor Circuit Low Voltage"],
          ["P0161","O2 Sensor Heater Circuit"]
        ],

        evap: [
          ["P0440","EVAP System Malfunction"],
          ["P0442","EVAP Small Leak Detected"],
          ["P0455","EVAP Large Leak Detected"],
          ["P0456","EVAP Very Small Leak Detected"],
          ["P0446","EVAP Vent Control Circuit"]
        ]

      };

      for (const group of Object.values(clusters)) {

        const codes = group.map(c => c[0]);

        if (codes.includes(code)) {

          return group
            .filter(c => c[0] !== code)
            .map(c => ({
              code: c[0],
              label: c[1]
            }));

        }

      }

      return [];
    }

    // Build Related Codes
    const related = buildClusterRelatedCodes(data.code);
    renderRelatedCodes(related);

    } catch (err) {
      setStatus("Server error.", "error");
    }
  }

  lookupBtn.addEventListener("click", lookup);
  clearBtn.addEventListener("click", () => {
  input.value = "";
  resultCard.classList.remove("show");
  resultCard.style.display = "none";
  setStatus("");
  lastQ = "";
  showSuggest(false);

  if (relatedWrap) relatedWrap.style.display = "none";
  if (relatedList) relatedList.innerHTML = "";

});

  input.addEventListener("keydown", e => {
    if (e.key === "Enter") lookup();
  });

  addToSelectionBtn?.addEventListener("click", () => {
    if (!lastLookedUpCode) {
      setStatus("Lookup a code first.", "error");
      return;
    }

    if (!selectedCodes.includes(lastLookedUpCode)) {
      selectedCodes.push(lastLookedUpCode);
      renderSelectedCodes();
      setStatus(`${lastLookedUpCode} added to selection.`, "success");
    } else {
      setStatus("Code already selected.", "error");
    }
  });

  sendSelectedBtn?.addEventListener("click", () => {
    if (!selectedCodes.length) {
      setStatus("Select at least one code first.", "error");
      return;
    }

    const joined = selectedCodes.join(",");
    window.location.href = `/estimator?obd=${encodeURIComponent(joined)}`;
  });

  clearSelectedBtn?.addEventListener("click", () => {
    selectedCodes = [];
    renderSelectedCodes();
    setStatus("Selection cleared.");
  });

  // Auto-load code from URL (for related code navigation)
  const params = new URLSearchParams(window.location.search);
  const prefill = params.get("prefill");

  if (prefill) {
    input.value = normalize(prefill);

    setTimeout(() => {
      lookup();
    }, 150);
  }
});