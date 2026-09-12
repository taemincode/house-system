// Real links are the fallback; enhance them with instant view switching.
(() => {
  const links = [...document.querySelectorAll('.event-tabs a')];
  const panels = ['upcoming', 'results'].map(id => document.getElementById(id));
  function renderFromURL() {
    const url = new URL(window.location.href);
    const view = url.searchParams.get('view') === 'results' ? 'results' : 'upcoming';
    panels.forEach(panel => { panel.hidden = panel.id !== view; });
    links.forEach(link => {
      if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    const target = url.hash && document.getElementById(decodeURIComponent(url.hash.slice(1)));
    if (target && !target.closest('[hidden]')) {
      if (target.matches('details')) target.open = true;
      target.scrollIntoView({block: 'start'});
    }
  }
  links.forEach(link => link.addEventListener('click', event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    history.pushState({}, '', link.href);
    renderFromURL();
  }));
  window.addEventListener('popstate', renderFromURL);
  renderFromURL();
})();
