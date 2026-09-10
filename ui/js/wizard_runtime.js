"use strict";

(function exposeWizardRuntime(global) {
  const byId = (id) => document.getElementById(id);
  const all = (selector) => Array.from(document.querySelectorAll(selector));

  function addFact(container, label, value) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    row.append(term, detail);
    container.append(row);
  }

  function createNavigation({
    state,
    pageOrder,
    renderFooter,
    focusPage = false,
  }) {
    return function setPage(page) {
      state.page = page;
      all("[data-page]").forEach((node) => {
        node.hidden = node.dataset.page !== page;
      });
      const activeIndex = pageOrder.indexOf(page);
      all("[data-step]").forEach((node) => {
        const index = pageOrder.indexOf(node.dataset.step);
        node.classList.toggle("is-active", index === activeIndex);
        node.classList.toggle("is-complete", index < activeIndex);
      });
      renderFooter();
      if (focusPage) {
        document.querySelector(`[data-page="${page}"]`)?.focus?.();
      }
    };
  }

  global.ThemeSchedulerWizardRuntime = Object.freeze({
    addFact,
    all,
    byId,
    createNavigation,
  });
})(window);
