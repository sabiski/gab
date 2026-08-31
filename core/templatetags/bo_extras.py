from django import template

register = template.Library()

PHARMACY_SECTION_ICONS = {
    "dashboard": "dashboard",
    "orders": "receipt_long",
    "prescription_detail": "description",
    "stocks": "inventory_2",
    "personnel": "groups",
    "stats": "analytics",
    "messages": "chat",
    "notifications": "notifications",
    "settings": "store",
    "subscription": "card_membership",
    "tips": "clinical_notes",
}

PHARMACY_JOB_ICONS = {
    "owner": "verified_user",
    "manager": "supervisor_account",
    "pharmacist": "medication",
    "preparer": "science",
    "cashier": "point_of_sale",
    "intern": "school",
}


@register.filter
def pharmacy_section_icon(section):
    return PHARMACY_SECTION_ICONS.get(section, "local_pharmacy")


@register.filter
def pharmacy_job_icon(job_role):
    return PHARMACY_JOB_ICONS.get(job_role, "badge")


@register.filter
def dict_get(mapping, key):
    if not mapping:
        return []
    try:
        return mapping.get(key, mapping.get(int(key), []))
    except (TypeError, ValueError):
        return mapping.get(key, [])


@register.filter
def courier_eligibility_info(profile):
    from core.courier_portal import courier_eligibility

    return courier_eligibility(profile)


@register.simple_tag
def authority_nav_link_class(nav_variant, section_key, active_section):
    """Classes CSS lien sidebar — Espace Autorités (admin ou autorité)."""
    key = (section_key or "").strip()
    active = (active_section or "").strip()
    is_active = key == active
    if nav_variant == "admin":
        base = "flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm font-semibold "
        return base + ("bg-white/15 text-secondary" if is_active else "text-white/70 hover:bg-white/10")
    return "auth-side-link" + (" is-active" if is_active else "")


@register.simple_tag
def insurer_nav_link_class(section_key, active_section, nav_variant="partner"):
    key = (section_key or "").strip()
    active = (active_section or "").strip()
    is_active = key == active
    if nav_variant == "admin":
        base = "flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm font-semibold "
        return base + ("bg-white/15 text-secondary" if is_active else "text-white/70 hover:bg-white/10")
    return "ins-side-link" + (" is-active" if is_active else "")


@register.simple_tag
def pharmacy_status_choices(order):
    from core.pharmacy_order_workflow import pharmacy_selectable_statuses

    return pharmacy_selectable_statuses(order)