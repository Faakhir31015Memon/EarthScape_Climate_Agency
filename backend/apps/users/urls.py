from django.urls import path

from apps.users import views

urlpatterns = [
    path("", views.UserListView.as_view(), name="users-list"),
    path("roles", views.RoleListView.as_view(), name="users-roles"),
    path("<str:user_id>", views.UserDetailView.as_view(), name="users-detail"),
]
