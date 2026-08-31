from django.core.paginator import Paginator


def paginate(request, queryset, per_page=20):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    return page_obj


def chart_last_n_days(model, date_field="created_at", n=14, extra_filter=None):
    """Retourne labels + counts pour les n derniers jours."""
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    today = timezone.localdate()
    start = today - timedelta(days=n - 1)
    qs = model.objects.filter(**{f"{date_field}__date__gte": start})
    if extra_filter:
        qs = qs.filter(**extra_filter)
    rows = (
        qs.annotate(d=TruncDate(date_field))
        .values("d")
        .annotate(c=Count("id"))
        .order_by("d")
    )
    by_day = {r["d"]: r["c"] for r in rows if r["d"]}
    labels, data = [], []
    for i in range(n):
        day = start + timedelta(days=i)
        labels.append(day.strftime("%d/%m"))
        data.append(by_day.get(day, 0))
    return labels, data
