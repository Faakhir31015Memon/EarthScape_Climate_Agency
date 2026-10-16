from django.urls import path

from apps.authentication import views

urlpatterns = [
    path("login", views.LoginView.as_view(), name="auth-login"),
    path("register", views.RegisterView.as_view(), name="auth-register"),
    path("refresh", views.RefreshView.as_view(), name="auth-refresh"),
    path("logout", views.LogoutView.as_view(), name="auth-logout"),
    path("me", views.MeView.as_view(), name="auth-me"),
    path("profile", views.ProfileView.as_view(), name="auth-profile"),
    path("password-change", views.PasswordChangeView.as_view(), name="auth-password-change"),
    path("password-reset", views.PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path("password-reset/confirm", views.PasswordResetConfirmView.as_view(),
         name="auth-password-reset-confirm"),
    path("registration-status", views.RegistrationStatusView.as_view(), name="auth-registration-status"),
]
