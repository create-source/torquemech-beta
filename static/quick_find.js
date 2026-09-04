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

    // Exact DTC always wins.
    if (code && normalizedTerm === code) {
      score += 2000;
    }

    // Exact title / component / symptom.
    if (title === normalizedTerm) {
      score += 1500;
    }

    // Strong prefix matches.
    if (code && code.startsWith(normalizedTerm)) {
      score += 1000;
    }

    if (title.startsWith(normalizedTerm)) {
      score += 850;
    }

    // Exact phrase anywhere in the record.
    if (title.includes(normalizedTerm)) {
      score += 650;
    }

    if (subtitle.includes(normalizedTerm)) {
      score += 450;
    }

    if (search.includes(normalizedTerm)) {
      score += 350;
    }

    // Multi-word matching.
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

      // Favor records where every query word appears in the title.
      const titleWordMatches = words.filter(function (word) {
        return title.includes(word);
      });

      if (titleWordMatches.length === words.length) {
        score += 250;
      }
    }

    // Search-type priorities.
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

function renderResults(resultsNode, results) {
  resultsNode.innerHTML = results
    .map(function (item) {
      const related = []
        .concat(item.related_codes || [])
        .concat(item.related_symptoms || [])
        .concat(item.related_repairs || [])
        .slice(0, 4);

      return (
        '<article class="tm-quick-find__result">' +

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

          renderPreviewList(
            "Likely involved",
            item.likely_involved
          ) +

          renderPreviewList(
            "Common diagnostic directions",
            item.diagnostic_directions
          ) +

          renderPreviewList(
            "Related",
            related
          ) +

          '<a class="tm-quick-find__result-footer" href="' +
            escapeHtml(item.href) +
          '">' +
            resultActionLabel(item) +
          "</a>" +

        "</article>"
      );
    })
    .join("");
}

  function initQuickFind(root) {
    const dataNode = root.querySelector("[data-quick-find-index]");
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

    let items = [];

    try {
      items = JSON.parse(dataNode.textContent || "[]");
    } catch (error) {
      items = [];
    }

    function search(term) {
      const normalizedTerm = normalize(term);

      clearButton.hidden = !input.value.trim();

      if (normalizedTerm.length < 2) {
        resultsNode.hidden = true;
        resultsNode.innerHTML = "";
        emptyNode.hidden = true;
        return [];
      }

      const ranked = items
        .map(function (item) {
          return {
            item: item,
            score: scoreItem(item, normalizedTerm),
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
        resultsNode.hidden = true;
        resultsNode.innerHTML = "";
        emptyNode.hidden = false;
        return [];
      }

      renderResults(resultsNode, ranked);
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

      // Do NOT automatically open the first result.
      // Let the technician review the diagnostic choices.
      search(input.value);

      if (!resultsNode.hidden) {
        resultsNode.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }
    });
  }

  document
    .querySelectorAll("[data-quick-find]")
    .forEach(initQuickFind);
})();