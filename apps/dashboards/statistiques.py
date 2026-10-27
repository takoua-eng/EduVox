"""Chiffres des tableaux de bord, calculés à partir des données réelles des modules (lecture seule)."""

from datetime import datetime, time, timedelta

from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import JournalConnexion, Utilisateur
from apps.assistant.models import SessionAssistant
from apps.courses.models import Consultation, Cours, Favori
from apps.quizzes.models import SEUIL_CERTIFICAT, Quiz, TentativeQuiz

JOURS_ACTIVITE = 14     # période des graphiques d'activité
JOURS_ACTIFS = 30       # un étudiant est « actif » s'il a consulté un cours ou passé un quiz pendant cette période
NB_RECENTS = 6

# Pourcentage d'une tentative corrigée (score / barème), calculé par la base
POURCENTAGE = ExpressionWrapper(F("score") * 100.0 / F("score_max"), output_field=FloatField())


def _il_y_a(jours):
    return timezone.now() - timedelta(days=jours)


def activite_par_jour(series, jours=JOURS_ACTIVITE):
    """Compte par jour (heure locale) pour plusieurs séries : [(nom, queryset, champ date), ...].

    Renvoie {"jours": ["12/10", ...], "series": [{"nom", "valeurs"}], "lignes": [(jour, v1, v2…)]}.
    Les jours sans activité valent 0 (sinon la courbe relierait des points éloignés).
    """
    aujourdhui = timezone.localdate()
    jours_liste = [aujourdhui - timedelta(days=n) for n in range(jours - 1, -1, -1)]
    debut = timezone.make_aware(datetime.combine(jours_liste[0], time.min))
    resultat = []
    for nom, queryset, champ in series:
        # order_by() vide : sans lui, le tri par défaut du modèle s'ajouterait au regroupement par jour
        comptes = dict(queryset.filter(**{f"{champ}__gte": debut}).order_by().annotate(jour=TruncDate(champ))
                       .values("jour").annotate(n=Count("pk")).values_list("jour", "n"))
        resultat.append({"nom": nom, "valeurs": [comptes.get(jour, 0) for jour in jours_liste]})
    etiquettes = [f"{jour:%d/%m}" for jour in jours_liste]
    return {
        "jours": etiquettes,
        "series": resultat,
        "lignes": [(etiquette, *(s["valeurs"][i] for s in resultat)) for i, etiquette in enumerate(etiquettes)],
        "total": sum(sum(s["valeurs"]) for s in resultat),
    }


def score_moyen(tentatives):
    """Score moyen (en %) des tentatives corrigées, ou None s'il n'y en a aucune."""
    moyenne = (tentatives.filter(statut=TentativeQuiz.Statut.CORRIGEE, score__isnull=False, score_max__gt=0)
               .aggregate(m=Avg(POURCENTAGE))["m"])
    return round(moyenne) if moyenne is not None else None


# ---------- Administrateur ----------

def stats_admin():
    tous = Utilisateur.objects.all()
    semaine = _il_y_a(7)
    connexions = JournalConnexion.objects.filter(date__gte=semaine)
    return {
        "stat_total": tous.count(),
        "stat_etudiants": tous.filter(role=Utilisateur.Role.ETUDIANT).count(),
        "stat_enseignants": tous.filter(role=Utilisateur.Role.ENSEIGNANT).count(),
        "stat_admins": tous.filter(role=Utilisateur.Role.ADMIN).count(),
        "stat_nouveaux": tous.filter(date_inscription__gte=semaine).count(),
        "stat_inactifs": tous.filter(is_active=False).count(),
        "derniers_inscrits": tous.order_by("-date_inscription")[:5],
        "stat_cours": Cours.objects.count(),
        "stat_cours_publies": Cours.objects.filter(Cours.filtre_publie(), masque=False, archive=False).count(),
        "stat_quiz_publies": Quiz.objects.filter(statut=Quiz.Statut.PUBLIE).count(),
        "stat_tentatives_semaine": TentativeQuiz.objects.filter(date_debut__gte=semaine).count(),
        "stat_connexions_semaine": connexions.filter(succes=True).count(),
        "stat_suspectes_semaine": connexions.filter(suspect=True).count(),
        "stat_echecs_semaine": connexions.filter(succes=False).count(),
        "activite": activite_par_jour([
            ("Connexions réussies", JournalConnexion.objects.filter(succes=True), "date"),
            ("Consultations de cours", Consultation.objects.all(), "date"),
            ("Quiz commencés", TentativeQuiz.objects.all(), "date_debut"),
        ]),
    }


# ---------- Enseignant ----------

def stats_enseignant(user):
    mes_cours = Cours.objects.filter(enseignant__utilisateur=user)
    mes_quiz = Quiz.objects.filter(enseignant__utilisateur=user)
    tentatives = TentativeQuiz.objects.filter(quiz__enseignant__utilisateur=user)
    consultations = Consultation.objects.filter(cours__enseignant__utilisateur=user)

    depuis = _il_y_a(JOURS_ACTIFS)
    actifs = (set(consultations.filter(date__gte=depuis).values_list("utilisateur", flat=True))
              | set(tentatives.filter(date_debut__gte=depuis).values_list("etudiant", flat=True)))
    return {
        "stat_cours": mes_cours.count(),
        "stat_cours_publies": mes_cours.filter(Cours.filtre_publie()).count(),
        "stat_cours_erreur": mes_cours.filter(statut=Cours.Statut.ERREUR).count(),
        "stat_quiz_publies": mes_quiz.filter(statut=Quiz.Statut.PUBLIE).count(),
        "stat_quiz": mes_quiz.count(),
        "stat_etudiants_actifs": len(actifs),
        "jours_actifs": JOURS_ACTIFS,
        "stat_score_moyen": score_moyen(tentatives),
        "stat_corrections_attente": tentatives.filter(statut=TentativeQuiz.Statut.SOUMISE).count(),
        "dernieres_tentatives": (tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS)
                                 .select_related("quiz", "etudiant").order_by("-date_soumission")[:5]),
        "cours_populaires": (mes_cours.annotate(nb_consultations=Count("consultations"))
                             .filter(nb_consultations__gt=0).order_by("-nb_consultations")[:5]),
        "activite": activite_par_jour([
            ("Consultations de mes cours", consultations, "date"),
            ("Quiz commencés", tentatives, "date_debut"),
        ]),
    }


# ---------- Étudiant ----------

def stats_etudiant(user):
    tentatives = TentativeQuiz.objects.filter(etudiant=user)
    corrigees = tentatives.filter(statut=TentativeQuiz.Statut.CORRIGEE, score__isnull=False, score_max__gt=0)
    nb_corrigees = corrigees.count()
    nb_reussis = corrigees.annotate(pct=POURCENTAGE).filter(pct__gte=SEUIL_CERTIFICAT).count()
    return {
        "stat_score_moyen": score_moyen(tentatives),
        "stat_quiz_termines": tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS).count(),
        "stat_quiz_reussis": nb_reussis,
        "stat_taux_reussite": round(100 * nb_reussis / nb_corrigees) if nb_corrigees else None,
        "seuil_reussite": SEUIL_CERTIFICAT,
        "stat_cours_consultes": Consultation.objects.filter(utilisateur=user).values("cours").distinct().count(),
        "stat_favoris": Favori.objects.filter(utilisateur=user).count(),
        "stat_sessions_assistant": SessionAssistant.objects.filter(utilisateur=user).count(),
        "activite_recente": activite_recente_etudiant(user),
    }


def activite_recente_etudiant(user, limite=NB_RECENTS):
    """Dernières actions de l'étudiant, tous modules confondus, de la plus récente à la plus ancienne."""
    evenements = []
    for c in Consultation.objects.filter(utilisateur=user).select_related("cours")[:limite]:
        evenements.append({"date": c.date, "icone": "ri-book-open-line", "couleur": "primary",
                           "texte": f"Cours consulté : {c.cours.titre}", "lien": reverse("detail-cours", args=[c.cours_id])})
    for t in TentativeQuiz.objects.filter(etudiant=user).select_related("quiz")[:limite]:
        if t.statut == TentativeQuiz.Statut.CORRIGEE and t.pourcentage is not None:
            detail = f"{t.pourcentage} %"
        else:
            detail = t.get_statut_display().lower()
        evenements.append({"date": t.date_soumission or t.date_debut, "icone": "ri-checkbox-circle-line",
                           "couleur": "success", "texte": f"Quiz « {t.quiz.titre} » : {detail}",
                           "lien": reverse("mes-tentatives")})
    for s in SessionAssistant.objects.filter(utilisateur=user)[:limite]:
        evenements.append({"date": s.date_maj, "icone": "ri-mic-line", "couleur": "info",
                           "texte": f"Assistant : {s.titre}", "lien": reverse("session-assistant", args=[s.pk])})
    return sorted(evenements, key=lambda e: e["date"], reverse=True)[:limite]
