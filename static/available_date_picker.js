(() => {
  const pad = (value) => String(value).padStart(2, "0");
  const isoDate = (year, month, day) => `${year}-${pad(month + 1)}-${pad(day)}`;
  const monthKey = (value) => `${value.getFullYear()}-${pad(value.getMonth() + 1)}`;
  const parseLocalDate = (value) => {
    const parts = String(value || "").split("-").map(Number);
    return parts.length === 3 && parts.every(Number.isFinite)
      ? new Date(parts[0], parts[1] - 1, parts[2])
      : null;
  };

  document.querySelectorAll("[data-availability-picker]").forEach((picker) => {
    const input = picker.querySelector("[data-availability-date]");
    const grid = picker.querySelector("[data-availability-days]");
    const heading = picker.querySelector("[data-availability-month]");
    const status = picker.querySelector("[data-availability-status]");
    const timeInput = document.querySelector(picker.dataset.timeTarget || "");
    const timeMessage = document.querySelector(picker.dataset.messageTarget || "");
    const datesUrl = picker.dataset.datesUrl;
    const timesUrl = picker.dataset.timesUrl;
    const initialDate = parseLocalDate(input?.value);
    let shownMonth = initialDate || new Date();
    shownMonth = new Date(shownMonth.getFullYear(), shownMonth.getMonth(), 1);
    const cache = new Map();

    const resetTimes = (message = "") => {
      if (!timeInput) return;
      timeInput.innerHTML = '<option value="">Select an available time</option>';
      timeInput.disabled = true;
      if (timeMessage) timeMessage.textContent = message;
    };

    const loadTimes = async () => {
      if (!input?.value || !timesUrl) {
        resetTimes();
        return;
      }
      resetTimes("Checking available times…");
      try {
        const response = await fetch(`${timesUrl}?date=${encodeURIComponent(input.value)}`);
        if (!response.ok) throw new Error("availability");
        const result = await response.json();
        if (result.state !== "available") {
          resetTimes(result.message || "No appointment times are available for this day. Please choose another day.");
          const selectedDate = parseLocalDate(input.value);
          if (selectedDate) cache.delete(monthKey(selectedDate));
          render();
          return;
        }
        const selectedTime = timeInput.dataset.selectedTime || "";
        result.times.forEach((slot) => {
          const option = document.createElement("option");
          option.value = slot.value;
          option.textContent = slot.label;
          option.selected = slot.value === selectedTime;
          timeInput.appendChild(option);
        });
        timeInput.disabled = false;
        timeInput.dataset.selectedTime = "";
        if (timeMessage) timeMessage.textContent = "";
      } catch (_) {
        resetTimes("Available times could not be loaded. Please try another day.");
      }
    };

    const render = async () => {
      const key = monthKey(shownMonth);
      heading.textContent = shownMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" });
      status.textContent = "Checking available dates…";
      grid.setAttribute("aria-busy", "true");
      try {
        if (!cache.has(key)) {
          const response = await fetch(`${datesUrl}?month=${encodeURIComponent(key)}`);
          if (!response.ok) throw new Error("availability");
          const result = await response.json();
          cache.set(key, new Map(result.days.map((day) => [day.date, day.available])));
        }
        const availability = cache.get(key);
        const firstOffset = new Date(shownMonth.getFullYear(), shownMonth.getMonth(), 1).getDay();
        const daysInMonth = new Date(shownMonth.getFullYear(), shownMonth.getMonth() + 1, 0).getDate();
        grid.innerHTML = "";
        for (let index = 0; index < firstOffset; index += 1) {
          const spacer = document.createElement("span");
          spacer.className = "tm-availability-day is-outside";
          grid.appendChild(spacer);
        }
        for (let day = 1; day <= daysInMonth; day += 1) {
          const value = isoDate(shownMonth.getFullYear(), shownMonth.getMonth(), day);
          const button = document.createElement("button");
          const available = availability.get(value) === true;
          button.type = "button";
          button.className = "tm-availability-day";
          button.textContent = String(day);
          button.disabled = !available;
          button.setAttribute("aria-label", `${new Date(shownMonth.getFullYear(), shownMonth.getMonth(), day).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}${available ? ", available" : ", unavailable"}`);
          button.setAttribute("aria-pressed", String(input.value === value));
          if (available) {
            button.addEventListener("click", () => {
              input.value = value;
              grid.querySelectorAll("[aria-pressed='true']").forEach((item) => item.setAttribute("aria-pressed", "false"));
              button.setAttribute("aria-pressed", "true");
              status.textContent = `Selected ${button.getAttribute("aria-label").replace(", available", "")}.`;
              input.dispatchEvent(new Event("change", { bubbles: true }));
              loadTimes();
            });
          }
          grid.appendChild(button);
        }
        status.textContent = input.value ? `Selected ${parseLocalDate(input.value).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}.` : "Choose a highlighted available date.";
      } catch (_) {
        grid.innerHTML = "";
        status.textContent = "Available dates could not be loaded. Please try again.";
      } finally {
        grid.removeAttribute("aria-busy");
      }
    };

    picker.querySelector("[data-availability-prev]")?.addEventListener("click", () => {
      shownMonth = new Date(shownMonth.getFullYear(), shownMonth.getMonth() - 1, 1);
      render();
    });
    picker.querySelector("[data-availability-next]")?.addEventListener("click", () => {
      shownMonth = new Date(shownMonth.getFullYear(), shownMonth.getMonth() + 1, 1);
      render();
    });
    render();
    if (input?.value) loadTimes();
  });
})();
