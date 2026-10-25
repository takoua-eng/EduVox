/* Outils webcam partagés (reconnaissance faciale EduVox) */
window.EduVoxCamera = {
  flux: null,

  async demarrer(video) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("Votre navigateur ne permet pas d'utiliser la caméra.");
    }
    try {
      this.flux = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false });
    } catch (e) {
      throw new Error("Accès à la caméra refusé ou caméra introuvable. Autorisez la caméra dans votre navigateur.");
    }
    video.srcObject = this.flux;
    await video.play();
  },

  arreter() {
    if (this.flux) this.flux.getTracks().forEach((piste) => piste.stop());
    this.flux = null;
  },

  capturer(video) {
    if (!video.videoWidth) {
      throw new Error("La caméra n'affiche aucune image. Autorisez la caméra, fermez les autres onglets ou applications qui l'utilisent, puis rechargez la page.");
    }
    
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    return canvas.toDataURL("image/jpeg", 0.9);
  },

  attendre(ms) {
    return new Promise((ok) => setTimeout(ok, ms));
  },

  async compteARebours(element, secondes) {
    for (let s = secondes; s > 0; s--) {
      element.textContent = s;
      element.classList.remove("d-none");
      await this.attendre(1000);
    }
    element.classList.add("d-none");
  },

  async envoyer(url, donnees) {
    const jeton = document.querySelector("[name=csrfmiddlewaretoken]").value;
    const reponse = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": jeton },
      body: JSON.stringify(donnees),
    });
    const resultat = await reponse.json().catch(() => ({
      ok: false,
      message: `Erreur du serveur (code ${reponse.status}). Regardez le terminal où tourne runserver.`,
    }));
    return resultat;
  },
};