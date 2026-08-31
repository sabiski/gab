from django.urls import path

from backoffice import views as bo_views

urlpatterns = [
    path("connexion/", bo_views.login_view, name="login"),
    path("verification-2fa/", bo_views.two_factor_verify_view, name="two_factor_verify"),
    path("verification-2fa/renvoyer/", bo_views.two_factor_resend_view, name="two_factor_resend"),
    path("inscription/", bo_views.register_view, name="register"),
    path("inscription-autorite/", bo_views.authority_register_view, name="authority_register"),
    path("inscription-assurance/", bo_views.insurer_register_view, name="insurer_register"),
    path("deconnexion/", bo_views.logout_view, name="logout"),
]
