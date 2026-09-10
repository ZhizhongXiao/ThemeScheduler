"use strict";

(function exposeBrand(global) {
  const MARKUP = `
    <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <circle class="brand-face" cx="16" cy="16" r="12"></circle>
      <path class="brand-night" d="M16 4a12 12 0 0 1 0 24Z"></path>
      <path class="brand-hand" d="M16 8v8l4.5 3"></path>
      <circle class="brand-hub" cx="16" cy="16" r="1.7"></circle>
    </svg>
  `.trim();

  function render(container) {
    if (!(container instanceof Element)) {
      throw new TypeError("ThemeScheduler brand container must be an Element.");
    }
    container.innerHTML = MARKUP;
  }

  function renderAll(root = document) {
    root.querySelectorAll("[data-theme-scheduler-brand]").forEach(render);
  }

  global.ThemeSchedulerBrand = Object.freeze({ render, renderAll });
  renderAll();
})(window);
