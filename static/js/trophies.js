document.querySelectorAll('model-viewer.trophy-model').forEach((viewer) => {
  viewer.addEventListener('error', () => {
    viewer.querySelector('.trophy-model-status').textContent =
      'The 3D preview could not load. Please refresh to try again.';
    viewer.showPoster();
  });
});
