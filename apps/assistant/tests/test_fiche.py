"""Fiche de révision générée à partir de la conversation (Gemini simulé)."""

import os
from unittest import mock

from django.urls import reverse

from apps.assistant.models import FicheRevision, JournalIA, MessageVocal, SessionAssistant
from apps.assistant.services import ErreurAssistant, langue_de_la_session, valider_fiche

from .base import BaseAssistantTestCase, patch_gemini

FICHE = {
    "titre": "Matrices inversibles",
    "resume": "Une matrice carrée est inversible si son déterminant est non nul.",
    "notions": [{"nom": "Déterminant", "explication": "Nombre associé à une matrice carrée."}],
    "definitions": [{"terme": "Matrice inverse", "definition": "A⁻¹ telle que A × A⁻¹ = I."}],
    "exemples": ["La matrice identité est inversible."],
    "a_retenir": ["det(A) ≠ 0 ⇔ A inversible"],
    "questions": [{"question": "Quand A est-elle inversible ?", "reponse": "Quand det(A) ≠ 0."}],
}


class FicheTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR,
                                    texte="Quand une matrice est-elle inversible ?", langue="fr")
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.ASSISTANT,
                                    texte="Quand son déterminant est non nul.", langue="fr")
        self.url = reverse("fiche-assistant", args=[self.session.pk])
        self.url_generer = reverse("generer-fiche-assistant", args=[self.session.pk])

    def generer(self, reponses=FICHE):
        with patch_gemini(reponses=reponses), self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url_generer)

    def test_generation_et_affichage(self):
        self.assertContains(self.client.get(self.url), "Générer la fiche")
        self.assertRedirects(self.generer(), self.url)
        fiche = FicheRevision.objects.get(session=self.session)
        self.assertEqual(fiche.statut, FicheRevision.Statut.TERMINEE)
        self.assertEqual((fiche.langue, fiche.nb_messages), ("fr", 2))
        page = self.client.get(self.url)
        for texte in ("Matrices inversibles", "Notions clés", "Déterminant", "À retenir", "Teste-toi",
                      "Quand A est-elle inversible ?", "PDF / Imprimer"):
            self.assertContains(page, texte)
        self.assertEqual(self.client.get(reverse("etat-fiche-assistant", args=[self.session.pk])).json(),
                         {"statut": "TERMINEE", "fini": True})
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.FICHE, succes=True).exists())

    def test_fiche_perimee_apres_de_nouveaux_messages(self):
        self.generer()
        self.assertNotContains(self.client.get(self.url), "La conversation a continué")
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR, texte="Et le rang ?")
        self.assertContains(self.client.get(self.url), "La conversation a continué")

    def test_conversation_sans_reponse(self):
        vide = SessionAssistant.objects.create(utilisateur=self.etudiant, titre="Vide")
        reponse = self.client.post(reverse("generer-fiche-assistant", args=[vide.pk]), follow=True)
        self.assertContains(reponse, "Posez au moins une question")
        self.assertFalse(FicheRevision.objects.filter(session=vide).exists())

    def test_reponse_ia_vide_ou_illisible(self):
        self.generer(reponses={"titre": "x", "resume": "", "notions": [], "a_retenir": [], "questions": []})
        fiche = FicheRevision.objects.get(session=self.session)
        self.assertEqual(fiche.statut, FicheRevision.Statut.ERREUR)
        self.assertContains(self.client.get(self.url), "Réessayer")
        with mock.patch("apps.assistant.services.time.sleep"):
            self.generer(reponses="pas du json")
        self.assertEqual(FicheRevision.objects.get(session=self.session).statut, FicheRevision.Statut.ERREUR)

    def test_limite_horaire(self):
        with mock.patch.dict(os.environ, {"ASSISTANT_FICHES_PAR_HEURE": "1"}):
            self.generer()
            reponse = self.client.post(self.url_generer, follow=True)
        self.assertContains(reponse, "Limite de fiches atteinte")

    def test_acces(self):
        self.connecter(self.autre_etudiant)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url_generer).status_code, 404)
        self.connecter(self.prof)
        self.assertEqual(self.client.get(self.url).status_code, 403)


class ValidationFicheTests(BaseAssistantTestCase):
    def test_nettoyage_et_bornes(self):
        donnees = dict(FICHE, notions=[{"nom": f"N{i}", "explication": "x"} for i in range(20)] + [{"nom": ""}, "texte"],
                       a_retenir=["  a  ", "", None, 3])
        fiche = valider_fiche(donnees)
        self.assertEqual(len(fiche["notions"]), 8)
        self.assertEqual(fiche["a_retenir"], ["a", "3"])

    def test_fiche_vide_refusee(self):
        with self.assertRaises(ErreurAssistant):
            valider_fiche({"titre": "x", "resume": "", "notions": "pas une liste"})

    def test_langue_majoritaire(self):
        for langue in ("en", "en", "fr"):
            MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR, texte="q", langue=langue)
        self.assertEqual(langue_de_la_session(self.session), "en")
        self.session.langue = "ar"
        self.assertEqual(langue_de_la_session(self.session), "ar")
