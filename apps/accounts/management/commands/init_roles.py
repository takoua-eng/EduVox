from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from apps.accounts.models import Utilisateur
from apps.accounts.roles import NOM_GROUPE, PERMISSIONS_PAR_ROLE


class Command(BaseCommand):
    help = "Crée les groupes (rôles), leur attribue les permissions et range les utilisateurs."

    def handle(self, *args, **options):
        for role, codes in PERMISSIONS_PAR_ROLE.items():
            groupe, _ = Group.objects.get_or_create(name=NOM_GROUPE[role])
            permissions = Permission.objects.filter(
                content_type__app_label="accounts", codename__in=codes
            )
            groupe.permissions.set(permissions)
            self.stdout.write(self.style.SUCCESS(
                f"Groupe « {groupe.name} » : {permissions.count()} permission(s)"
            ))

        for utilisateur in Utilisateur.objects.all():
            utilisateur.synchroniser_groupe()
        self.stdout.write(self.style.SUCCESS(
            f"{Utilisateur.objects.count()} utilisateur(s) rangé(s) dans leur groupe."
        ))