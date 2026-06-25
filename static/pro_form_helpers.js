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

  function normalizeMileageBeforeSubmit(form) {
    form.addEventListener("submit", () => {
      form.querySelectorAll("[data-pro-mileage-input]").forEach((input) => {
        input.value = digitsOnly(input.value);
      });
    });
  }

  function init(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-pro-phone-input]").forEach(bindPhoneInput);
    scope.querySelectorAll("[data-pro-mileage-input]").forEach(bindMileageInput);
    scope.querySelectorAll("form").forEach(normalizeMileageBeforeSubmit);
  }

  window.TorqueMechProForms = {
    formatPhone,
    formatMileage,
    init,
  };

  document.addEventListener("DOMContentLoaded", () => init(document));
})();
