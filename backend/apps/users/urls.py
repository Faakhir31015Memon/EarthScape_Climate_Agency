"""URLs for administrator user management, mounted at ``/api/v1/users/``."""
from django.urls import path

from apps.users import views

urlpatterns = [
    path("", views.UserListView.as_view(), name="users-list"),
    path("roles", views.RoleCatalogueView.as_view(), name="users-roles"),
    path("<str:account_id>", views.UserDetailView.as_view(), name="user-detail"),
    path("<str:account_id>/activate", views.UserActivateView.as_view(), name="user-activate"),
    path("<str:account_id>/deactivate", views.UserDeactivateView.as_view(), name="user-deactivate"),
    path("<str:account_id>/role", views.UserRoleView.as_view(), name="user-role"),
    path("<str:account_id>/reset-password", views.UserPasswordResetView.as_view(),
         name="user-reset-password"),
    path("<str:account_id>/unlock", views.UserUnlockView.as_view(), name="user-unlock"),
    path("<str:account_id>/sessions", views.UserSessionsView.as_view(), name="user-sessions"),
]
