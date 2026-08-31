"""Accès sécurisé aux fichiers d'ordonnance (aperçu iframe / téléchargement)."""

import mimetypes



from django.contrib.auth.decorators import login_required

from django.http import FileResponse, Http404

from django.shortcuts import get_object_or_404

from django.views.decorators.clickjacking import xframe_options_sameorigin



from backoffice.decorators import admin_roles, pharmacy_roles, support_roles

from core.pharmacy_access import pharmacy_ids_for_user

from orders.models import Order, Prescription





def _user_can_view_prescription(user, rx: Prescription) -> bool:

    if not user.is_authenticated:

        return False

    if rx.client_id == user.id:

        return True

    role = getattr(user, "role", None)

    if role in admin_roles or role in support_roles:

        return True

    if role in pharmacy_roles:

        pharmacy_ids = pharmacy_ids_for_user(user)

        if rx.pharmacy_id and rx.pharmacy_id in pharmacy_ids:

            return True

        if Order.objects.filter(linked_prescription=rx, pharmacy_id__in=pharmacy_ids).exists():

            return True

    return False





def _user_can_view_order(user, order: Order) -> bool:

    if not user.is_authenticated:

        return False

    if order.client_id == user.id:

        return True

    role = getattr(user, "role", None)

    if role in admin_roles or role in support_roles:

        return True

    if role in pharmacy_roles and order.pharmacy_id in pharmacy_ids_for_user(user):

        return True

    return False





def _file_response(field_file, download_name=None):

    if not field_file or not field_file.name:

        raise Http404("Fichier introuvable.")

    try:

        handle = field_file.open("rb")

    except FileNotFoundError:

        raise Http404("Fichier introuvable sur le serveur.") from None

    content_type, _ = mimetypes.guess_type(field_file.name)

    name = download_name or field_file.name.split("/")[-1]

    response = FileResponse(handle, content_type=content_type or "application/octet-stream")

    response["Content-Disposition"] = f'inline; filename="{name}"'

    return response





@login_required(login_url="login")

@xframe_options_sameorigin

def prescription_file_view(request, pk):

    rx = get_object_or_404(Prescription, pk=pk)

    if not _user_can_view_prescription(request.user, rx):

        raise Http404()

    return _file_response(rx.file, rx.file.name.split("/")[-1])





@login_required(login_url="login")

@xframe_options_sameorigin

def order_prescription_file_view(request, order_id):

    order = get_object_or_404(Order, pk=order_id)

    if not _user_can_view_order(request.user, order):

        raise Http404()

    if not order.prescription:

        raise Http404()

    return _file_response(order.prescription, f"ordonnance-commande-{order.code}.pdf")

