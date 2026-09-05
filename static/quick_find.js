(function () {
  function normalize(value) {
    return (value || "")
      .toString()
      .toLowerCase()
      .trim()
      .replace(/[_-]+/g, " ")
      .replace(/[^\w\s]+/g, " ")
      .replace(/\s+/g, " ");
  }

  function queryWords(term) {
    return normalize(term)
      .split(" ")
      .filter(function (word) {
        return word.length >= 2;
      });
  }

  function scoreItem(item, term) {
    const normalizedTerm = normalize(term);
    const words = queryWords(normalizedTerm);

    const search = normalize(item.search || "");
    const title = normalize(item.title || "");
    const subtitle = normalize(item.subtitle || "");
    const code = normalize(item.code || "");

    const combined = [code, title, subtitle, search]
      .filter(Boolean)
      .join(" ");

    if (!normalizedTerm) {
      return -1;
    }

    let score = 0;

    if (code && normalizedTerm === code) {
      score += 2000;
    }

    if (title === normalizedTerm) {
      score += 1500;
    }

    if (code && code.startsWith(normalizedTerm)) {
      score += 1000;
    }

    if (title.startsWith(normalizedTerm)) {
      score += 850;
    }

    if (title.includes(normalizedTerm)) {
      score += 650;
    }

    if (subtitle.includes(normalizedTerm)) {
      score += 450;
    }

    if (search.includes(normalizedTerm)) {
      score += 350;
    }

    if (words.length) {
      const matchedWords = words.filter(function (word) {
        return combined.includes(word);
      });

      if (!matchedWords.length) {
        return -1;
      }

      if (matchedWords.length === words.length) {
        score += 300;
      } else {
        score += matchedWords.length * 60;
      }

      const titleWordMatches = words.filter(function (word) {
        return title.includes(word);
      });

      if (titleWordMatches.length === words.length) {
        score += 250;
      }
    }

    if (item.type === "OBD Code") {
      score += 80;
    }

    if (item.type === "Symptom") {
      score += 60;
    }

    if (item.type === "Repair Guide") {
      score += 45;
    }

    if (item.type === "Cost Guide") {
      score += 20;
    }

    return score;
  }

  function resultActionLabel(item) {
    if (item.type === "OBD Code") {
      return "Open Code →";
    }

    if (item.type === "Symptom") {
      return "Open Diagnostic Path →";
    }

    if (item.type === "Repair Guide") {
      return "Open Repair Guide →";
    }

    if (item.type === "Cost Guide") {
      return "View Repair Details →";
    }

    return "Open →";
  }

  function escapeHtml(value) {
    return (value || "")
      .toString()
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function parseJsonNode(node, fallback) {
    if (!node) {
      return fallback;
    }

    try {
      return JSON.parse(node.textContent || "");
    } catch (error) {
      return fallback;
    }
  }

  function findingTitle(item) {
    const code = (item.code || "").toString().trim();
    const title = (item.title || "").toString().trim();

    if (code && title) {
      const normalizedTitle = title.toUpperCase();

      if (normalizedTitle.startsWith(code.toUpperCase())) {
        return title;
      }

      return code + " — " + title;
    }

    return title || code || "Diagnostic Finding";
  }

  function findingRecommendation(item) {
    const directions = Array.isArray(item.diagnostic_directions)
      ? item.diagnostic_directions.filter(Boolean)
      : [];

    if (directions.length) {
      return directions.slice(0, 3).join(" ");
    }

    if (item.subtitle) {
      return item.subtitle.toString().trim();
    }

    if (item.type === "OBD Code") {
      return "Verify the diagnostic cause before replacing components.";
    }

    if (item.type === "Symptom") {
      return "Inspect and verify the cause of the reported symptom before repair.";
    }

    if (item.type === "Repair Guide") {
      return "Verify inspection findings before proceeding with the repair.";
    }

    return "Verify the condition and recommended repair before proceeding.";
  }

  function renderPreviewList(label, items) {
    if (!Array.isArray(items) || !items.length) {
      return "";
    }

    return (
      '<div class="tm-quick-find__preview">' +
        '<div class="tm-quick-find__preview-label">' +
          escapeHtml(label) +
        "</div>" +
        '<ul class="tm-quick-find__preview-list">' +
          items
            .slice(0, 3)
            .map(function (item) {
              return "<li>" + escapeHtml(item) + "</li>";
            })
            .join("") +
        "</ul>" +
      "</div>"
    );
  }

  function renderFindingAction(item) {
    return (
      '<div class="tm-quick-find__workflow">' +
        '<button ' +
          'type="button" ' +
          'class="tm-quick-find__add-finding" ' +
          'data-add-finding' +
        ">" +
          "+ Add as Finding" +
        "</button>" +

        '<div class="tm-quick-find__finding-panel" data-finding-panel hidden>' +
          '<div class="tm-quick-find__finding-heading">Add to TorqueMech</div>' +

          '<div class="tm-quick-find__field">' +
            '<label>Customer</label>' +
            '<select data-finding-customer>' +
              '<option value="">Select customer</option>' +
            "</select>" +
          "</div>" +

          '<div class="tm-quick-find__field">' +
            '<label>Vehicle</label>' +
            '<select data-finding-vehicle disabled>' +
              '<option value="">Select vehicle</option>' +
            "</select>" +
          "</div>" +

          '<p class="tm-quick-find__finding-error" data-finding-error hidden></p>' +

          '<div class="tm-quick-find__finding-actions">' +
            '<button type="button" class="tm-quick-find__finding-cancel" data-finding-cancel>' +
              "Cancel" +
            "</button>" +

            '<button type="button" class="tm-quick-find__finding-submit" data-finding-submit>' +
              "Add Finding" +
            "</button>" +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function renderResults(resultsNode, results) {
    if (!results.length) {
      resultsNode.innerHTML = "";
      return;
    }

    const best = results[0];
    const relatedResults = results.slice(1);

    const bestRelated = []
      .concat(best.related_codes || [])
      .concat(best.related_symptoms || [])
      .concat(best.related_repairs || [])
      .slice(0, 4);

    const bestMatchHtml =
      '<section class="tm-quick-find__best-match">' +
        '<div class="tm-quick-find__best-label">Best Match</div>' +

        '<article class="tm-quick-find__result tm-quick-find__result--best" data-result-index="0">' +

          '<a class="tm-quick-find__result-main" href="' +
            escapeHtml(best.href) +
          '">' +

            '<div class="tm-quick-find__result-type">' +
              escapeHtml(best.type || "Diagnostic Result") +
            "</div>" +

            '<div class="tm-quick-find__result-title">' +
              escapeHtml(best.title || "") +
            "</div>" +

            (best.subtitle
              ? '<p class="tm-quick-find__result-subtitle">' +
                  escapeHtml(best.subtitle) +
                "</p>"
              : "") +

          "</a>" +

          renderPreviewList("Likely involved", best.likely_involved) +

          renderPreviewList(
            "Common diagnostic directions",
            best.diagnostic_directions
          ) +

          renderPreviewList("Related", bestRelated) +

          '<div class="tm-quick-find__result-actions">' +
            '<a class="tm-quick-find__result-footer" href="' +
              escapeHtml(best.href) +
            '">' +
              resultActionLabel(best) +
            "</a>" +

            renderFindingAction(best) +
          "</div>" +

        "</article>" +
      "</section>";

    const relatedHtml = relatedResults.length
      ? (
        '<section class="tm-quick-find__related-results">' +
          '<div class="tm-quick-find__related-heading">Related Results</div>' +

          '<div class="tm-quick-find__related-grid">' +
            relatedResults
              .map(function (item, index) {
                return (
                  '<article class="tm-quick-find__result tm-quick-find__result--compact" ' +
                    'data-result-index="' + (index + 1) + '">' +

                    '<a class="tm-quick-find__result-main" href="' +
                      escapeHtml(item.href) +
                    '">' +

                      '<div class="tm-quick-find__result-type">' +
                        escapeHtml(item.type || "Diagnostic Result") +
                      "</div>" +

                      '<div class="tm-quick-find__result-title">' +
                        escapeHtml(item.title || "") +
                      "</div>" +

                      (item.subtitle
                        ? '<p class="tm-quick-find__result-subtitle">' +
                            escapeHtml(item.subtitle) +
                          "</p>"
                        : "") +

                    "</a>" +

                    '<div class="tm-quick-find__result-actions">' +
                      '<a class="tm-quick-find__result-footer" href="' +
                        escapeHtml(item.href) +
                      '">' +
                        resultActionLabel(item) +
                      "</a>" +

                      renderFindingAction(item) +
                    "</div>" +

                  "</article>"
                );
              })
              .join("") +
          "</div>" +
        "</section>"
      )
      : "";

    resultsNode.innerHTML = bestMatchHtml + relatedHtml;
  }

  function initQuickFind(root) {
    const dataNode = root.querySelector("[data-quick-find-index]");
    const customersNode = root.querySelector("[data-quick-find-customers]");
    const vehiclesNode = root.querySelector("[data-quick-find-vehicles]");
    const csrfNode = root.querySelector("[data-quick-find-csrf]");

    const inputWrap = root.querySelector(".quick-find-input-wrap");
    const input = inputWrap ? inputWrap.querySelector("input") : null;

    const clearButton = inputWrap
      ? inputWrap.querySelector(":scope > .quick-find-clear")
      : null;

    const form = root.querySelector(".tm-quick-find__form");
    const resultsNode = root.querySelector("[data-quick-find-results]");
    const emptyNode = root.querySelector("[data-quick-find-empty]");

    if (
      !dataNode ||
      !inputWrap ||
      !input ||
      !clearButton ||
      !form ||
      !resultsNode ||
      !emptyNode
    ) {
      return;
    }

    const items = parseJsonNode(dataNode, []);
    const customers = parseJsonNode(customersNode, []);
    const vehiclesByCustomer = parseJsonNode(vehiclesNode, {});
    const csrfToken = csrfNode ? csrfNode.value : "";

    let currentResults = [];

    function customerLabel(customer) {
      const name = [
        customer.first_name || "",
        customer.last_name || ""
      ]
        .join(" ")
        .trim();

      return name || "Customer #" + customer.id;
    }

    function vehicleLabel(vehicle) {
      const vehicleName = [
        vehicle.year || "",
        vehicle.make || "",
        vehicle.model || ""
      ]
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();

      const mileage = Number(vehicle.mileage || 0);

      if (vehicleName && mileage > 0) {
        return vehicleName + " — " + mileage.toLocaleString() + " mi";
      }

      return vehicleName || "Vehicle #" + vehicle.id;
    }

    function populateCustomers(select) {
      select.innerHTML =
        '<option value="">Select customer</option>' +
        customers
          .map(function (customer) {
            return (
              '<option value="' +
              escapeHtml(customer.id) +
              '">' +
              escapeHtml(customerLabel(customer)) +
              "</option>"
            );
          })
          .join("");
    }

    function populateVehicles(customerId, select) {
      const vehicles = vehiclesByCustomer[String(customerId)] || [];

      select.innerHTML =
        '<option value="">Select vehicle</option>' +
        vehicles
          .map(function (vehicle) {
            return (
              '<option value="' +
              escapeHtml(vehicle.id) +
              '">' +
              escapeHtml(vehicleLabel(vehicle)) +
              "</option>"
            );
          })
          .join("");

      select.disabled = !customerId || !vehicles.length;
    }

    function selectedVehicle(customerId, vehicleId) {
      const vehicles = vehiclesByCustomer[String(customerId)] || [];

      return vehicles.find(function (vehicle) {
        return String(vehicle.id) === String(vehicleId);
      }) || null;
    }

    function closeFindingPanels(exceptPanel) {
      root
        .querySelectorAll("[data-finding-panel]")
        .forEach(function (panel) {
          if (panel !== exceptPanel) {
            panel.hidden = true;
          }
        });
    }

    async function submitFinding(article, panel, item) {
      const customerSelect = panel.querySelector("[data-finding-customer]");
      const vehicleSelect = panel.querySelector("[data-finding-vehicle]");
      const errorNode = panel.querySelector("[data-finding-error]");
      const submitButton = panel.querySelector("[data-finding-submit]");

      const customerId = customerSelect.value;
      const vehicleId = vehicleSelect.value;

      errorNode.hidden = true;
      errorNode.textContent = "";

      if (!customerId) {
        errorNode.textContent = "Choose a customer.";
        errorNode.hidden = false;
        customerSelect.focus();
        return;
      }

      if (!vehicleId) {
        errorNode.textContent = "Choose a vehicle.";
        errorNode.hidden = false;
        vehicleSelect.focus();
        return;
      }

      const vehicle = selectedVehicle(customerId, vehicleId);

      const formData = new URLSearchParams();

      formData.set("csrf_token", csrfToken);
      formData.set("finding", findingTitle(item));
      formData.set("recommendation", findingRecommendation(item));
      formData.set("severity", "Medium");
      formData.set("status", "Open");
      formData.set("request_type", "finding");

      if (vehicle && vehicle.mileage) {
        formData.set("mileage", vehicle.mileage);
      }

      submitButton.disabled = true;
      submitButton.textContent = "Adding...";

      try {
        const response = await fetch(
          "/pro/customers/" +
            encodeURIComponent(customerId) +
            "/vehicles/" +
            encodeURIComponent(vehicleId) +
            "/findings",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
            },
            body: formData.toString(),
            credentials: "same-origin",
            redirect: "follow"
          }
        );

        if (!response.ok) {
          throw new Error("Unable to create finding.");
        }

        window.location.href = response.url;
      } catch (error) {
        errorNode.textContent =
          "TorqueMech could not add this finding. Please try again.";
        errorNode.hidden = false;

        submitButton.disabled = false;
        submitButton.textContent = "Add Finding";
      }
    }

    function wireFindingActions() {
      resultsNode
        .querySelectorAll(".tm-quick-find__result")
        .forEach(function (article) {
          const resultIndex = Number(article.dataset.resultIndex);
          const item = currentResults[resultIndex];

          if (!item) {
            return;
          }

          const openButton = article.querySelector("[data-add-finding]");
          const panel = article.querySelector("[data-finding-panel]");
          const customerSelect = article.querySelector(
            "[data-finding-customer]"
          );
          const vehicleSelect = article.querySelector(
            "[data-finding-vehicle]"
          );
          const cancelButton = article.querySelector(
            "[data-finding-cancel]"
          );
          const submitButton = article.querySelector(
            "[data-finding-submit]"
          );

          if (
            !openButton ||
            !panel ||
            !customerSelect ||
            !vehicleSelect ||
            !cancelButton ||
            !submitButton
          ) {
            return;
          }

          populateCustomers(customerSelect);

          openButton.addEventListener("click", function () {
            closeFindingPanels(panel);

            panel.hidden = !panel.hidden;

            if (!panel.hidden) {
              customerSelect.focus();
            }
          });

          customerSelect.addEventListener("change", function () {
            populateVehicles(customerSelect.value, vehicleSelect);
          });

          cancelButton.addEventListener("click", function () {
            panel.hidden = true;
          });

          submitButton.addEventListener("click", function () {
            submitFinding(article, panel, item);
          });
        });
    }

    function search(term) {
      const normalizedTerm = normalize(term);

      clearButton.hidden = !input.value.trim();

      if (normalizedTerm.length < 2) {
        currentResults = [];
        resultsNode.hidden = true;
        resultsNode.innerHTML = "";
        emptyNode.hidden = true;
        return [];
      }

      const ranked = items
        .map(function (item) {
          return {
            item: item,
            score: scoreItem(item, normalizedTerm)
          };
        })
        .filter(function (entry) {
          return entry.score >= 0;
        })
        .sort(function (left, right) {
          if (right.score !== left.score) {
            return right.score - left.score;
          }

          return (left.item.title || "").localeCompare(
            right.item.title || ""
          );
        })
        .slice(0, 10)
        .map(function (entry) {
          return entry.item;
        });

      if (!ranked.length) {
        currentResults = [];
        resultsNode.hidden = true;
        resultsNode.innerHTML = "";
        emptyNode.hidden = false;
        return [];
      }

      currentResults = ranked;

      renderResults(resultsNode, ranked);
      wireFindingActions();

      resultsNode.hidden = false;
      emptyNode.hidden = true;

      return ranked;
    }

    input.addEventListener("input", function () {
      search(input.value);
    });

    clearButton.addEventListener("click", function () {
      input.value = "";
      search("");
      input.focus();
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();

      search(input.value);

      if (!resultsNode.hidden) {
        resultsNode.scrollIntoView({
          behavior: "smooth",
          block: "nearest"
        });
      }
    });
  }

  document
    .querySelectorAll("[data-quick-find]")
    .forEach(initQuickFind);
})();