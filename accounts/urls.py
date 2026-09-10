from django.urls import path

from backoffice import views as bo_views

urlpatterns = [
    path("connexion/", bo_views.login_view, name="login"),
    path("mot-de-passe-oublie/", bo_views.password_reset_request_view, name="password_reset"),
    path(
        "mot-de-passe-oublie/termine/",
        bo_views.password_reset_complete_view,
        name="password_reset_complete",
    ),
    path(
        "mot-de-passe-oublie/<uidb64>/<token>/",
        bo_views.password_reset_confirm_view,
        name="password_reset_confirm",
    ),
    path("verification-2fa/", bo_views.two_factor_verify_view, name="two_factor_verify"),
    path("verification-2fa/renvoyer/", bo_views.two_factor_resend_view, name="two_factor_resend"),
    path("inscription/", bo_views.register_view, name="register"),
    path("inscription-autorite/", bo_views.authority_register_view, name="authority_register"),
    path("inscription-assurance/", bo_views.insurer_register_view, name="insurer_register"),
    path("deconnexion/", bo_views.logout_view, name="logout"),
]
