(function () {
  function formatPhone(value) {
    const digits = String(value || "").replace(/\D/g, "").slice(0, 10);
    if (digits.length <= 3) return digits;
    if (digits.length <= 6) return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
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
    input.value = formatPhone(input.value);
    input.addEventListener("input", () => {
      input.value = formatPhone(input.value);
    });
  }

  function bindMileageInput(input) {
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

    const button = document.createElement("button");
    button.type = "button";
    button.className = "tm-date-clear-button";
    button.setAttribute("aria-label", "Clear date");
    button.title = "Clear date";
    button.textContent = "x";
    button.addEventListener("click", () => {
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus();
    });
    wrapper.appendChild(button);
  }

  function ensureDateClearStyles() {
    if (document.getElementById("tm-pro-date-clear-styles")) return;
    const style = document.createElement("style");
    style.id = "tm-pro-date-clear-styles";
    style.textContent = `
      .tm-date-clear-field {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 42px;
        gap: 8px;
        align-items: center;
      }
      .tm-date-clear-field > input[type="date"] {
        width: 100%;
        min-width: 0;
      }
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
      .tm-date-clear-button:hover,
      .tm-date-clear-button:focus-visible {
        border-color: #0f766e;
        color: #0f766e;
        outline: none;
      }
    `;
    document.head.appendChild(style);
  }

  function normalizeMileageBeforeSubmit(form) {
    form.addEventListener("submit", () => {
      form.querySelectorAll("[data-pro-mileage-input]").forEach((input) => {
        input.value = digitsOnly(input.value);
      });
    });
  }

  function init(root) {
    const scope = root || document;
    ensureDateClearStyles();
    scope.querySelectorAll("[data-pro-phone-input]").forEach(bindPhoneInput);
    scope.querySelectorAll("[data-pro-mileage-input]").forEach(bindMileageInput);
    scope.querySelectorAll('input[type="date"]').forEach(bindDateInput);
    scope.querySelectorAll("form").forEach(normalizeMileageBeforeSubmit);
  }

  window.TorqueMechProForms = {
    formatPhone,
    formatMileage,
    bindDateInput,
    init,
  };

  document.addEventListener("DOMContentLoaded", () => init(document));
})();
