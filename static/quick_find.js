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

  function scoreItem(item, term) {
    const search = normalize(item.search || "");
    const title = normalize(item.title || "");
    const subtitle = normalize(item.subtitle || "");
    const code = normalize(item.code || "");

    if (!term || !(search.includes(term) || title.includes(term) || subtitle.includes(term) || code === term)) {
      return -1;
    }

    let score = 0;
    if (code && term === code) score += 1000;
    if (title === term) score += 700;
    if (title.startsWith(term)) score += 300;
    if (code && code.startsWith(term)) score += 260;
    if (title.includes(term)) score += 180;
    if (subtitle.includes(term)) score += 100;
    if (search.includes(term)) score += 60;

    if (item.type === "Cost Guide" && title.includes(term)) score += 35;
    if (item.type === "OBD Code" && code && code.startsWith(term)) score += 25;

    return score;
  }

  function renderResults(resultsNode, results) {
    resultsNode.innerHTML = results
      .map(function (item) {
        return (
          '<a class="tm-quick-find__result" href="' +
          item.href +
          '">' +
          '<div class="tm-quick-find__result-type">' +
          item.type +
          "</div>" +
          '<div class="tm-quick-find__result-title">' +
          item.title +
          "</div>" +
          (item.subtitle
            ? '<p class="tm-quick-find__result-subtitle">' + item.subtitle + "</p>"
            : "") +
          '<div class="tm-quick-find__result-footer">Open page →</div>' +
          "</a>"
        );
      })
      .join("");
  }

  function initQuickFind(root) {
    const dataNode = root.querySelector("[data-quick-find-index]");
    const inputWrap = root.querySelector(".quick-find-input-wrap");
    const input = inputWrap ? inputWrap.querySelector("input") : null;
    const clearButton = inputWrap ? inputWrap.querySelector(":scope > .quick-find-clear") : null;
    const form = root.querySelector(".tm-quick-find__form");
    const resultsNode = root.querySelector("[data-quick-find-results]");
    const emptyNode = root.querySelector("[data-quick-find-empty]");

    if (!dataNode || !inputWrap || !input || !clearButton || !form || !resultsNode || !emptyNode) return;

    let items = [];
    try {
      items = JSON.parse(dataNode.textContent || "[]");
    } catch (error) {
      items = [];
    }

    function search(term) {
      const normalizedTerm = normalize(term);
      if (clearButton) {
        clearButton.hidden = !input.value.trim();
      }

      if (normalizedTerm.length < 2) {
        resultsNode.hidden = true;
        resultsNode.innerHTML = "";
        emptyNode.hidden = true;
        return [];
      }

      const ranked = items
        .map(function (item) {
          return { item: item, score: scoreItem(item, normalizedTerm) };
        })
        .filter(function (entry) {
          return entry.score >= 0;
        })
        .sort(function (left, right) {
          if (right.score !== left.score) return right.score - left.score;
          return (left.item.title || "").localeCompare(right.item.title || "");
        })
        .slice(0, 8)
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

    if (clearButton) {
      clearButton.addEventListener("click", function () {
        input.value = "";
        search("");
        input.focus();
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      const results = search(input.value);
      if (results.length) {
        window.location.href = results[0].href;
      }
    });
  }

  document.querySelectorAll("[data-quick-find]").forEach(initQuickFind);
})();
