from django.contrib.auth.mixins import UserPassesTestMixin


class RoleRequiredMixin(UserPassesTestMixin):
    roles_autorises = []

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_superuser or user.role in self.roles_autorises)