// Native forms remain usable without JavaScript; the server validates every save.
document.querySelectorAll('[data-switch]').forEach((select) => {
  const update = () => {
    document.querySelectorAll(`[data-switch-group="${select.dataset.switch}"]`).forEach((group) => {
      const active = group.dataset.switchValue === select.value;
      group.hidden = !active;
      group.disabled = !active;
      group.querySelectorAll('[data-required]').forEach((input) => { input.required = active; });
    });
    if (select.name === 'status') {
      const form = select.form;
      form.elements.date.required = select.value === 'completed';
      form.elements.description.required = select.value === 'upcoming';
      form.querySelectorAll('[name^="coins_"]').forEach((input) => { input.required = select.value === 'completed'; });
    }
  };
  select.addEventListener('change', update);
  update();
});

document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
