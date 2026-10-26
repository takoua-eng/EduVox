/* EduVox – petites corrections d'accessibilité côté navigateur */
(function () {
  function corrigerBarresDeDefilement() {
    // La bibliothèque perfect-scrollbar ajoute des <div> dans la liste du menu (<ul>) :
    // on les cache aux lecteurs d'écran et on les retire de la navigation au clavier.
    document.querySelectorAll('.ps__rail-x, .ps__rail-y').forEach(function (rail) {
      rail.setAttribute('role', 'presentation');
      rail.setAttribute('aria-hidden', 'true');
    });
    document.querySelectorAll('.ps__thumb-x, .ps__thumb-y').forEach(function (barre) {
      barre.setAttribute('tabindex', '-1');
    });
  }
  window.addEventListener('load', function () {
    corrigerBarresDeDefilement();
    setTimeout(corrigerBarresDeDefilement, 500);
  });
})();