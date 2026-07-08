(function () {
  function formatPhone(value) {
    const digits = String(value || "").replace(/\D/g, "").slice(0, 10);
    if (!digits) return "";
    if (digits.length <= 3) return `(${digits}`;
    if (digits.length <= 6) return `(${digits.slice(0, 3)})${digits.slice(3)}`;
    return `(${digits.slice(0, 3)})${digits.slice(3, 6)}-${digits.slice(6)}`;
  }

  function digitsOnly(value) {
    return String(value || "").replace(/\D/g, "");
  }

  function formatMileage(value) {
    const digits = digitsOnly(value);
    if (!digits) return "";
    return Number(digits).toLocaleString();
  }

  function bindPhoneInput(input) {
    if (input.dataset.proPhoneBound === "1") return;
    input.dataset.proPhoneBound = "1";
    input.value = formatPhone(input.value);
    input.addEventListener("input", () => {
      input.value = formatPhone(input.value);
    });
    input.addEventListener("blur", () => {
      input.value = formatPhone(input.value);
    });
  }

  function bindMileageInput(input) {
    if (input.dataset.proMileageBound === "1") return;
    input.dataset.proMileageBound = "1";
    input.value = formatMileage(input.value);
    input.addEventListener("input", () => {
      input.value = formatMileage(input.value);
    });
    input.addEventListener("blur", () => {
      input.value = formatMileage(input.value);
    });
  }

  function bindDateInput(input) {
    if (input.dataset.proDateBound === "1") return;
    input.dataset.proDateBound = "1";
    const wrapper = document.createElement("span");
    wrapper.className = "tm-date-clear-field";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    const pickerButton = document.createElement("button");
    pickerButton.type = "button";
    pickerButton.className = "tm-date-picker-button";
    pickerButton.setAttribute("aria-label", "Open calendar");
    pickerButton.title = "Open calendar";
    pickerButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="17" rx="2"></rect><path d="M8 2v4M16 2v4M3 10h18"></path></svg>';
    pickerButton.addEventListener("click", () => {
      input.focus();
      if (typeof input.showPicker === "function") {
        input.showPicker();
      }
    });
    wrapper.appendChild(pickerButton);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "tm-date-clear-button";
    button.setAttribute("aria-label", "Clear date");
    button.title = "Clear date";
    button.textContent = "x";
    const updateClearButton = () => {
      button.hidden = !String(input.value || "").trim();
    };
    button.addEventListener("click", () => {
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus();
      updateClearButton();
    });
    input.addEventListener("input", updateClearButton);
    input.addEventListener("change", updateClearButton);
    wrapper.appendChild(button);
    updateClearButton();
  }

  function ensureDateClearStyles() {
    if (document.getElementById("tm-pro-date-clear-styles")) return;
    const style = document.createElement("style");
    style.id = "tm-pro-date-clear-styles";
    style.textContent = `
      .tm-date-clear-field {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 42px 42px;
        gap: 8px;
        align-items: center;
      }
      .tm-date-clear-field > input[type="date"] {
        width: 100%;
        min-width: 0;
      }
      .tm-date-picker-button,
      .tm-date-clear-button {
        width: 42px;
        min-height: 42px;
        border: 1px solid rgba(15, 23, 42, 0.18);
        border-radius: 8px;
        background: #fff;
        color: #475569;
        font-size: 1rem;
        font-weight: 900;
        line-height: 1;
        cursor: pointer;
      }
      .tm-date-picker-button {
        display: inline-grid;
        place-items: center;
      }
      .tm-date-picker-button svg {
        width: 18px;
        height: 18px;
        fill: none;
        stroke: currentColor;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
      }
      .tm-date-picker-button:hover,
      .tm-date-picker-button:focus-visible,
      .tm-date-clear-button:hover,
      .tm-date-clear-button:focus-visible {
        border-color: #0f766e;
        color: #0f766e;
        outline: none;
      }
    `;
    document.head.appendChild(style);
  }

  function ensurePhotoUploadStyles() {
    if (document.getElementById("tm-pro-photo-upload-styles")) return;
    const style = document.createElement("style");
    style.id = "tm-pro-photo-upload-styles";
    style.textContent = `
      .tm-photo-input-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        margin: -1px;
        padding: 0;
        overflow: hidden;
        clip: rect(0 0 0 0);
        clip-path: inset(50%);
        border: 0;
        white-space: nowrap;
      }
      .tm-photo-upload-button {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: fit-content;
        min-height: 44px;
        border: 1px solid rgba(15, 118, 110, 0.28);
        border-radius: 8px;
        background: #0f766e;
        color: #fff;
        font-weight: 900;
        line-height: 1.1;
        padding: 11px 14px;
        cursor: pointer;
      }
      .tm-photo-upload-button:hover,
      .tm-photo-upload-button:focus-visible,
      .tm-photo-input-hidden:focus-visible + .tm-photo-upload-button {
        background: #115e59;
        outline: 3px solid rgba(20, 184, 166, 0.22);
        outline-offset: 2px;
      }
      .tm-photo-helper,
      .tm-photo-privacy,
      .tm-photo-selected-count {
        color: #64748b;
        font-size: 0.88rem;
        line-height: 1.35;
      }
      .tm-photo-privacy {
        color: #475569;
      }
      .tm-photo-selected-count {
        font-weight: 900;
      }
      @media (max-width: 640px) {
        .tm-photo-upload-button {
          width: 100%;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function photoSelectionLabel(count) {
    if (count === 1) return "1 photo selected";
    if (count > 1) return `${count} photos selected`;
    return "No photos selected";
  }

  function bindPhotoInput(input) {
    if (input.dataset.proPhotoBound === "1") return;
    input.dataset.proPhotoBound = "1";
    const countEl = document.querySelector(`[data-photo-selected-count="${input.id}"]`);
    const update = () => {
      if (!countEl) return;
      countEl.textContent = photoSelectionLabel(input.files ? input.files.length : 0);
    };
    input.addEventListener("change", update);
    update();
  }

  function normalizeMileageBeforeSubmit(form) {
    if (form.dataset.proMileageSubmitBound === "1") return;
    form.dataset.proMileageSubmitBound = "1";
    form.addEventListener("submit", () => {
      form.querySelectorAll("[data-pro-mileage-input]").forEach((input) => {
        input.value = digitsOnly(input.value);
      });
    });
  }

  function isPhoneLikeInput(input) {
    if (!(input instanceof HTMLInputElement)) return false;
    const haystack = [
      input.type,
      input.name,
      input.id,
      input.className,
      input.getAttribute("autocomplete"),
    ].join(" ").toLowerCase();
    return input.type === "tel" || haystack.includes("phone");
  }

  function init(root) {
    const scope = root || document;
    ensureDateClearStyles();
    ensurePhotoUploadStyles();
    scope.querySelectorAll("input").forEach((input) => {
      if (isPhoneLikeInput(input)) bindPhoneInput(input);
    });
    scope.querySelectorAll("[data-pro-mileage-input]").forEach(bindMileageInput);
    scope.querySelectorAll("[data-pro-photo-input]").forEach(bindPhotoInput);
    scope.querySelectorAll('input[type="date"]').forEach(bindDateInput);
    scope.querySelectorAll("form").forEach(normalizeMileageBeforeSubmit);
  }

  window.TorqueMechProForms = {
    formatPhone,
    formatMileage,
    bindDateInput,
    init,
  };
  window.TorqueMechPhone = {
    format: formatPhone,
    digitsOnly: (value) => digitsOnly(value).slice(0, 10),
    bind: bindPhoneInput,
  };

  document.addEventListener("DOMContentLoaded", () => init(document));
})();
