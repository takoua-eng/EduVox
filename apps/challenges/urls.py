from django.urls import path

from .views import defis, equipes, evenements, ia, livrables, taches

urlpatterns = [
    # Défis et jalons
    path("", defis.CatalogueView.as_view(), name="catalogue-defis"),
    path("creer/", defis.CreerDefiView.as_view(), name="creer-defi"),
    path("generer/", ia.GenererBriefView.as_view(), name="generer-brief"),
    path("generer/<int:pk>/", ia.ApercuBriefView.as_view(), name="apercu-brief"),
    path("<int:pk>/", defis.DetailDefiView.as_view(), name="detail-defi"),
    path("<int:pk>/modifier/", defis.ModifierDefiView.as_view(), name="modifier-defi"),
    path("<int:pk>/supprimer/", defis.SupprimerDefiView.as_view(), name="supprimer-defi"),
    path("<int:pk>/statut/<str:action>/", defis.ChangerStatutDefiView.as_view(), name="statut-defi"),
    path("<int:pk>/demarrer/", defis.DemarrerDefiView.as_view(), name="demarrer-defi"),
    path("<int:pk>/suivi/", defis.SuiviDefiView.as_view(), name="suivi-defi"),
    path("<int:pk>/jalons/ajouter/", defis.CreerJalonView.as_view(), name="creer-jalon"),
    path("jalons/<int:pk>/modifier/", defis.ModifierJalonView.as_view(), name="modifier-jalon"),
    path("jalons/<int:pk>/supprimer/", defis.SupprimerJalonView.as_view(), name="supprimer-jalon"),

    # Équipes et adhésions
    path("<int:pk>/equipes/creer/", equipes.CreerEquipeView.as_view(), name="creer-equipe"),
    path("equipes/<int:pk>/", equipes.EspaceEquipeView.as_view(), name="espace-equipe"),
    path("equipes/<int:pk>/modifier/", equipes.ModifierEquipeView.as_view(), name="modifier-equipe"),
    path("equipes/<int:pk>/rejoindre/", equipes.RejoindreEquipeView.as_view(), name="rejoindre-equipe"),
    path("equipes/<int:pk>/quitter/", equipes.QuitterEquipeView.as_view(), name="quitter-equipe"),
    path("equipes/<int:pk>/coordination/", equipes.TransfererCoordinationView.as_view(), name="transferer-coordination"),
    path("equipes/<int:pk>/reprendre/", equipes.ReprendreCoordinationView.as_view(), name="reprendre-coordination"),
    path("participations/<int:pk>/retirer/", equipes.RetirerDemandeView.as_view(), name="retirer-demande"),
    path("participations/<int:pk>/accepter/", equipes.AccepterDemandeView.as_view(), name="accepter-demande"),
    path("participations/<int:pk>/refuser/", equipes.RefuserDemandeView.as_view(), name="refuser-demande"),
    path("participations/<int:pk>/exclure/", equipes.ExclureMembreView.as_view(), name="exclure-membre"),

    # Espace d'équipe : onglets
    path("equipes/<int:pk>/taches/", equipes.TachesEquipeView.as_view(), name="taches-equipe"),
    path("equipes/<int:pk>/jalons/", equipes.JalonsEquipeView.as_view(), name="jalons-equipe"),
    path("equipes/<int:pk>/imprevus/", equipes.ImprevusEquipeView.as_view(), name="imprevus-equipe"),
    path("equipes/<int:pk>/livrables/", equipes.LivrablesEquipeView.as_view(), name="livrables-equipe"),

    # Tâches
    path("equipes/<int:pk>/taches/creer/", taches.CreerTacheView.as_view(), name="creer-tache"),
    path("taches/<int:pk>/", taches.DetailTacheView.as_view(), name="detail-tache"),
    path("taches/<int:pk>/modifier/", taches.ModifierTacheView.as_view(), name="modifier-tache"),
    path("taches/<int:pk>/deplacer/", taches.DeplacerTacheView.as_view(), name="deplacer-tache"),
    path("taches/<int:pk>/supprimer/", taches.SupprimerTacheView.as_view(), name="supprimer-tache"),

    # Livrables versionnés
    path("equipes/<int:pk>/livrables/creer/", livrables.CreerLivrableView.as_view(), name="creer-livrable"),
    path("livrables/<int:pk>/", livrables.DetailLivrableView.as_view(), name="detail-livrable"),
    path("livrables/<int:pk>/nouvelle-version/", livrables.NouvelleVersionView.as_view(), name="nouvelle-version"),
    path("versions/<int:pk>/modifier/", livrables.ModifierVersionView.as_view(), name="modifier-version"),
    path("versions/<int:pk>/soumettre/", livrables.SoumettreVersionView.as_view(), name="soumettre-version"),
    path("versions/<int:pk>/supprimer/", livrables.SupprimerVersionView.as_view(), name="supprimer-version"),
    path("versions/<int:pk>/evaluer/", livrables.EvaluerVersionView.as_view(), name="evaluer-version"),
    path("versions/<int:pk>/verifier/", livrables.VerifierVersionView.as_view(), name="verifier-version"),
    path("versions/<int:pk>/telecharger/", livrables.TelechargerVersionView.as_view(), name="telecharger-version"),

    # Imprévus et réponses d'adaptation
    path("<int:pk>/imprevus/", evenements.EvenementsDefiView.as_view(), name="evenements-defi"),
    path("<int:pk>/imprevus/creer/", evenements.CreerEvenementView.as_view(), name="creer-evenement"),
    path("<int:pk>/imprevus/proposer/", evenements.ProposerEvenementView.as_view(), name="proposer-evenement"),
    path("imprevus/<int:pk>/", evenements.DetailEvenementView.as_view(), name="detail-evenement"),
    path("imprevus/<int:pk>/modifier/", evenements.ModifierEvenementView.as_view(), name="modifier-evenement"),
    path("imprevus/<int:pk>/valider/", evenements.ValiderEvenementView.as_view(), name="valider-evenement"),
    path("imprevus/<int:pk>/publier/", evenements.PublierEvenementView.as_view(), name="publier-evenement"),
    path("imprevus/<int:pk>/supprimer/", evenements.SupprimerEvenementView.as_view(), name="supprimer-evenement"),
    path("imprevus/<int:pk>/equipes/<int:equipe_pk>/reponse/", evenements.ReponseEvenementView.as_view(),
         name="reponse-evenement"),
    path("imprevus/<int:pk>/equipes/<int:equipe_pk>/soumettre/", evenements.SoumettreReponseView.as_view(),
         name="soumettre-reponse"),

    # État des traitements IA
    path("ia/<int:pk>/etat/", ia.EtatDemandeView.as_view(), name="etat-demande-ia"),
]
