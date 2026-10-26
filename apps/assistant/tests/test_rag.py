"""Réponses appuyées sur le cours : découpage, index, recherche, citations (embeddings simulés)."""

from unittest import mock

from django.urls import reverse

from apps.assistant import rag
from apps.assistant.models import JournalIA, MessageVocal, PassageCours
from apps.assistant.services import configuration_reponse, preparer_question, requete_de_recherche
from apps.courses.models import Transcription

from .base import BaseAssistantTestCase, faux_client, patch_gemini

COURS = """Les matrices.

Une matrice carrée est inversible si et seulement si son déterminant est non nul. Le déterminant se calcule
par développement selon une ligne ou une colonne.

Les vecteurs propres.

Un vecteur propre d'une matrice est un vecteur non nul dont l'image est colinéaire à lui-même. La valeur propre
associée est le coefficient de proportionnalité. Les valeurs propres sont les racines du polynôme caractéristique.

La photosynthèse n'a rien à voir : elle transforme la lumière en énergie chimique dans les plantes. """ * 8


class DecoupageTests(BaseAssistantTestCase):
    def test_passages_bornes_et_chevauchants(self):
        passages = rag.decouper(COURS, taille=300, chevauchement=120)
        self.assertGreater(len(passages), 3)
        self.assertTrue(all(20 < len(p) <= 300 for p in passages))
        # le début d'un passage reprend la fin du précédent
        self.assertTrue(any(p2.split(". ")[0] in p1 for p1, p2 in zip(passages, passages[1:])))

    def test_phrase_interminable_coupee(self):
        passages = rag.decouper("mot " * 600, taille=300, chevauchement=0)
        self.assertTrue(all(len(p) <= 300 for p in passages))
        self.assertEqual(sum(len(p.split()) for p in passages), 600)

    def test_texte_vide(self):
        self.assertEqual(rag.decouper("   \n\n "), [])

    def test_citations(self):
        sources = [{"n": 1}, {"n": 2}, {"n": 3}]
        self.assertEqual(rag.citations("Voir [1] et [2][3], ou [1, 3].", sources), [1, 2, 3])
        self.assertEqual(rag.citations("Source inventée [9] et tableau [a].", sources), [])


class IndexTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        Transcription.objects.create(cours=self.cours, texte=COURS)

    def test_indexation_puis_cache(self):
        client = faux_client()
        espion = mock.Mock(wraps=client.models.embed_content)
        client.models.embed_content = espion
        with mock.patch("apps.assistant.services._client", return_value=client):
            passages = rag.indexer(self.cours)
            self.assertEqual(len(passages), PassageCours.objects.filter(cours=self.cours).count())
            self.assertGreater(len(passages), 1)
            rag.indexer(self.cours)
            self.assertEqual(espion.call_count, 1, "le même texte ne doit pas être revectorisé")
            self.cours.transcription.texte = COURS + " Nouveau chapitre sur les déterminants."
            self.cours.transcription.save()
            self.cours.refresh_from_db()
            rag.indexer(self.cours)
            self.assertEqual(espion.call_count, 2, "un texte modifié doit être réindexé")
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.INDEXATION, succes=True).exists())

    def test_recherche_trouve_le_bon_passage(self):
        with patch_gemini():
            resultats = rag.rechercher(self.cours, "Qu'est-ce qu'une valeur propre et un vecteur propre ?")
        self.assertEqual(len(resultats), rag.NB_PASSAGES)
        meilleur = max(resultats, key=lambda r: r[1])[0]
        self.assertIn("propre", meilleur.texte)
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.RECHERCHE).exists())

    def test_cours_sans_texte(self):
        self.cours.transcription.delete()
        self.cours.resume.delete()
        self.cours.refresh_from_db()
        self.assertEqual(rag.indexer(self.cours), [])

    def test_sources_enregistrees_et_consigne_de_citation(self):
        with patch_gemini():
            question, reponse = preparer_question(self.session, "Comment savoir si une matrice est inversible ?")
            config = configuration_reponse(self.session, question, reponse)
        reponse.refresh_from_db()
        self.assertEqual([s["n"] for s in reponse.sources], [1, 2, 3, 4])
        self.assertIn("[1]", config.system_instruction)
        self.assertIn("cite ceux que tu utilises", config.system_instruction)
        self.assertTrue(any("déterminant" in s["extrait"] for s in reponse.sources))

    def test_repli_sur_le_resume_si_les_embeddings_echouent(self):
        client = faux_client()
        client.models.embed_content = mock.Mock(side_effect=ConnectionError())
        with mock.patch("apps.assistant.services._client", return_value=client), \
                self.assertLogs("apps.assistant.services", "WARNING"):
            question, reponse = preparer_question(self.session, "Qu'est-ce qu'un déterminant ?")
            config = configuration_reponse(self.session, question, reponse)
        self.assertEqual(reponse.sources, [])
        self.assertIn("Algèbre linéaire", config.system_instruction)  # résumé du cours en repli
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.INDEXATION, succes=False).exists())

    def test_cours_non_visible_jamais_cherche(self):
        self.session.cours = self.cours_brouillon
        with patch_gemini():
            question, reponse = preparer_question(self.session, "Qu'est-ce qu'un déterminant ?")
            configuration_reponse(self.session, question, reponse)
        self.assertEqual(reponse.sources, [])
        self.assertFalse(PassageCours.objects.filter(cours=self.cours_brouillon).exists())

    def test_relance_courte_inclut_la_question_precedente(self):
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR,
                                    texte="Qu'est-ce qu'une valeur propre ?")
        question = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR,
                                               texte="Et pourquoi ?")
        self.assertEqual(requete_de_recherche(question), "Qu'est-ce qu'une valeur propre ?\nEt pourquoi ?")

    def test_affichage_des_citations(self):
        self.connecter()
        with patch_gemini(reponses="Elle est inversible si son déterminant est non nul [1][3]. Voir aussi [9]."):
            donnees = self.client.post(reverse("question-assistant", args=[self.session.pk]),
                                       {"texte": "Quand une matrice est-elle inversible ?"},
                                       HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()
        html = self.client.get(reverse("fragment-message-assistant", args=[donnees["reponse_id"]])).content.decode()
        self.assertIn(f'href="#src-{donnees["reponse_id"]}-1"', html)
        self.assertIn(f'id="src-{donnees["reponse_id"]}-3"', html)
        self.assertNotIn(f'id="src-{donnees["reponse_id"]}-2"', html)  # non cité : pas affiché
        self.assertIn("[9]", html)  # numéro inventé : laissé tel quel, sans lien
        self.assertIn("2 passages du cours cités", html)


class IndexationALaCreationTests(BaseAssistantTestCase):
    def test_session_liee_a_un_cours_indexe_le_cours(self):
        Transcription.objects.create(cours=self.cours, texte=COURS)
        self.connecter()
        with patch_gemini(), self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("creer-session-assistant"), {"titre": "Algèbre", "cours": self.cours.pk})
        self.assertTrue(PassageCours.objects.filter(cours=self.cours).exists())
