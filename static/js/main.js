// Native <details> keeps the house menu usable even without JavaScript.
const houseMenu = document.querySelector('.house-nav');
if (houseMenu) {
  document.addEventListener('click', (event) => {
    if (!houseMenu.contains(event.target)) houseMenu.open = false;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && houseMenu.open) {
      houseMenu.open = false;
      houseMenu.querySelector('summary').focus();
    }
  });
  document.addEventListener('focusin', (event) => {
    if (!houseMenu.contains(event.target)) houseMenu.open = false;
  });
}

// Animate visual totals once per page load; accessible totals stay unchanged.
const coinTotals = [...document.querySelectorAll('[data-count-up]')];
if (coinTotals.length) {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const counters = coinTotals.map((element) => ({
    value: Number(element.dataset.countUp),
    output: element.firstElementChild,
    finalText: element.dataset.final,
  })).filter(({ value }) => Number.isSafeInteger(value) && value > 0);

  if (!reducedMotion.matches && counters.length) {
    const formatter = new Intl.NumberFormat('en-US');
    const duration = 1100;
    const startedAt = performance.now();
    let frame;

    const finish = () => {
      cancelAnimationFrame(frame);
      counters.forEach(({ output, finalText }) => { output.textContent = finalText; });
      reducedMotion.removeEventListener('change', finish);
      window.removeEventListener('pagehide', finish);
    };

    const tick = (now) => {
      const progress = Math.min((now - startedAt) / duration, 1);
      if (progress >= 1 || reducedMotion.matches) {
        finish();
        return;
      }
      const eased = 1 - Math.pow(1 - progress, 3);
      counters.forEach(({ value, output }) => {
        output.textContent = formatter.format(Math.floor(value * eased));
      });
      frame = requestAnimationFrame(tick);
    };

    counters.forEach(({ output }) => { output.textContent = '0'; });
    reducedMotion.addEventListener('change', finish);
    window.addEventListener('pagehide', finish);
    frame = requestAnimationFrame(tick);
  }
}
