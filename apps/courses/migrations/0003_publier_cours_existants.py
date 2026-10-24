from django.db import migrations


def publier_cours_existants(apps, schema_editor):
    """Les cours créés avant la migration 0002 restent visibles des étudiants."""
    Cours = apps.get_model("courses", "Cours")
    Cours.objects.update(publication="publie")
    # Nombre de mots des textes déjà extraits (temps de lecture estimé)
    Transcription = apps.get_model("courses", "Transcription")
    for transcription in Transcription.objects.all():
        Cours.objects.filter(pk=transcription.cours_id).update(nb_mots=len(transcription.texte.split()))


class Migration(migrations.Migration):
    # Séparée de 0002 : PostgreSQL refuse de modifier les données et de créer
    # des index sur la même table dans une seule transaction.

    dependencies = [
        ("courses", "0002_ameliorations"),
    ]

    operations = [
        migrations.RunPython(publier_cours_existants, migrations.RunPython.noop),
    ]
