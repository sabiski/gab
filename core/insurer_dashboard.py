"""Tableau de bord assureur — 100 % calculé sur les données réelles.

Inspiré des portails gestionnaires des grands assureurs santé (file de travail,
délai de décision, taux d'acceptation, engagement, sinistralité par pharmacie
et par médicament, alertes fraude / plafonds, créances pharmacies).

Sources pour l'organisme connecté :
- demandes liées aux commandes Gab'Pharma (InsuranceClaim) ;
- dossiers de prise en charge patient PC-XXXXXX (CoverageRequest) ;
- tiers payant encaissé au comptoir par les pharmacies (CounterSale) ;
- relevés mensuels envoyés par les pharmacies (InsuranceStatement).
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Count, F, Sum
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClientProfile, InsuranceMemberSubscription
from orders.models import CounterSale, CounterSaleItem, OrderItem
from payments.models import (
    CoverageInvoiceLine,
    CoverageRequest,
    InsuranceClaim,
    InsuranceStatement,
    InsuranceSubscriptionPlan,
    InsurerFraudAlert,
)

PERIODS = {
    "7": (7, "7 derniers jours"),
    "30": (30, "30 derniers jours"),
    "90": (90, "3 derniers mois"),
    "365": (365, "12 derniers mois"),
}
DEFAULT_PERIOD = "30"
SLA_HOURS = 48

PC_PENDING = {CoverageRequest.Status.SENT, CoverageRequest.Status.UNDER_REVIEW}
PC_INFO = {CoverageRequest.Status.INFO_NEEDED}
PC_ACCEPTED = {
    CoverageRequest.Status.ACCEPTED,
    CoverageRequest.Status.READY,
    CoverageRequest.Status.COMPLETED,
    CoverageRequest.Status.EXPIRED,
}
PC_REJECTED = {CoverageRequest.Status.REJECTED}
CLAIM_ACCEPTED = {InsuranceClaim.Status.APPROVED, InsuranceClaim.Status.PAID}

_MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
_MONTHS_LONG = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                "août", "septembre", "octobre", "novembre", "décembre"]


def fmt_int(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def _pct(part, whole) -> float:
    return round(part * 100 / whole, 1) if whole else 0.0


def _trend(current, previous) -> dict | None:
    """Variation réelle vs période précédente (None si pas de base de comparaison)."""
    if not previous:
        return None if not current else {"label": "Nouveau", "direction": "up"}
    delta = (current - previous) * 100 / previous
    if abs(delta) < 0.05:
        return {"label": "Stable", "direction": "flat"}
    return {
        "label": f"{'+' if delta > 0 else '−'}{abs(delta):.1f} %".replace(".", ","),
        "direction": "up" if delta > 0 else "down",
    }


def _ago(dt) -> str:
    seconds = int((timezone.now() - dt).total_seconds())
    if seconds < 3600:
        return f"il y a {max(1, seconds // 60)} min"
    if seconds < 86400:
        return f"il y a {seconds // 3600} h"
    days = seconds // 86400
    return f"il y a {days} j"


@dataclass
class Window:
    start: datetime
    end: datetime
    prev_start: datetime
    days: int
    key: str
    label: str


def build_window(period_key: str | None) -> Window:
    key = period_key if period_key in PERIODS else DEFAULT_PERIOD
    days, label = PERIODS[key]
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min), tz)
    start = end - timedelta(days=days)
    return Window(start=start, end=end, prev_start=start - timedelta(days=days), days=days, key=key, label=label)


def _buckets(win: Window):
    """Découpage du graphique : jours (≤ 30 j), semaines (90 j), mois (12 mois)."""
    tz = timezone.get_current_timezone()
    out = []
    if win.days <= 30:
        for i in range(win.days):
            d0 = win.start + timedelta(days=i)
            out.append((d0, d0 + timedelta(days=1), timezone.localtime(d0).strftime("%d/%m")))
    elif win.days <= 90:
        cursor = win.start
        while cursor < win.end:
            nxt = min(cursor + timedelta(days=7), win.end)
            out.append((cursor, nxt, "S. " + timezone.localtime(cursor).strftime("%d/%m")))
            cursor = nxt
    else:
        local_end = timezone.localdate()
        first = date(local_end.year - 1, local_end.month, 1)
        for i in range(13):
            m = first.month - 1 + i
            y, mo = first.year + m // 12, m % 12 + 1
            d0 = timezone.make_aware(datetime(y, mo, 1), tz)
            m2 = mo % 12 + 1
            d1 = timezone.make_aware(datetime(y + (mo == 12), m2, 1), tz)
            if d1 <= win.start:
                continue
            out.append((max(d0, win.start), min(d1, win.end), f"{_MONTHS[mo - 1]} {str(y)[2:]}"))
    return out


def _sum(qs, field):
    return qs.aggregate(s=Sum(field))["s"] or 0


class InsurerDashboard:
    def __init__(self, profile, period_key: str | None = None):
        self.profile = profile
        self.provider = profile.insurance_provider
        self.win = build_window(period_key)
        pid = self.provider.pk if self.provider else None
        self.claims = InsuranceClaim.objects.filter(provider_id=pid)
        self.pcs = CoverageRequest.objects.filter(provider_id=pid)
        self.counter = CounterSale.objects.filter(
            insurance_provider_id=pid, status=CounterSale.Status.COMPLETED, insurance_amount__gt=0
        )
        self.statements = InsuranceStatement.objects.filter(provider_id=pid).exclude(
            status=InsuranceStatement.Status.DRAFT
        )

    # ── Agrégats par période ───────────────────────────────────────────
    def _received(self, a, b):
        return (
            self.claims.filter(created_at__gte=a, created_at__lt=b).count()
            + self.pcs.filter(created_at__gte=a, created_at__lt=b).count()
        )

    def _accepted_count(self, a, b):
        return (
            self.claims.filter(created_at__gte=a, created_at__lt=b, status__in=CLAIM_ACCEPTED).count()
            + self.pcs.filter(created_at__gte=a, created_at__lt=b, status__in=PC_ACCEPTED).count()
        )

    def _rejected_count(self, a, b):
        return (
            self.claims.filter(created_at__gte=a, created_at__lt=b, status=InsuranceClaim.Status.REJECTED).count()
            + self.pcs.filter(created_at__gte=a, created_at__lt=b, status__in=PC_REJECTED).count()
        )

    def _engaged(self, a, b) -> dict:
        claims = _sum(self.claims.filter(created_at__gte=a, created_at__lt=b, status__in=CLAIM_ACCEPTED), "amount")
        pcs = _sum(self.pcs.filter(created_at__gte=a, created_at__lt=b, status__in=PC_ACCEPTED), "insurance_amount")
        counter = _sum(self.counter.filter(created_at__gte=a, created_at__lt=b), "insurance_amount")
        return {"claims": claims, "pcs": pcs, "counter": counter, "total": claims + pcs + counter}

    def _decision_hours(self, a, b) -> float | None:
        durations = []
        for created, accepted, updated, status in self.pcs.filter(
            created_at__gte=a, created_at__lt=b
        ).exclude(status__in=PC_PENDING | PC_INFO).values_list("created_at", "accepted_at", "updated_at", "status"):
            decided = accepted or updated
            if decided and decided >= created:
                durations.append((decided - created).total_seconds() / 3600)
        for created, updated in self.claims.filter(
            created_at__gte=a, created_at__lt=b
        ).exclude(status=InsuranceClaim.Status.PENDING).values_list("created_at", "updated_at"):
            if updated >= created:
                durations.append((updated - created).total_seconds() / 3600)
        return round(sum(durations) / len(durations), 1) if durations else None

    # ── File de travail ────────────────────────────────────────────────
    def work_queue(self, limit=8) -> dict:
        now = timezone.now()
        sla = now - timedelta(hours=SLA_HOURS)
        items = []
        for c in self.claims.filter(status=InsuranceClaim.Status.PENDING).select_related(
            "client", "order", "order__pharmacy"
        ).order_by("created_at")[:limit]:
            items.append({
                "kind": "Commande", "icon": "shopping_bag",
                "ref": c.order.code if c.order_id else f"DP-{c.pk}",
                "who": c.client.get_full_name() or c.client.username,
                "where": c.order.pharmacy.name if c.order_id and c.order.pharmacy_id else "—",
                "amount": c.amount, "created_at": c.created_at, "late": c.created_at < sla,
                "url": reverse("bo_insurer_claim_detail", args=[c.pk]),
                "status_label": "À valider",
            })
        for r in self.pcs.filter(status__in=PC_PENDING | PC_INFO).select_related(
            "client", "pharmacy"
        ).order_by("created_at")[:limit]:
            items.append({
                "kind": "Prise en charge", "icon": "medical_information",
                "ref": r.reference,
                "who": r.client.get_full_name() or r.client.username,
                "where": r.pharmacy.name if r.pharmacy_id else "—",
                "amount": r.insurance_amount or r.estimated_amount,
                "created_at": r.created_at, "late": r.created_at < sla and r.status in PC_PENDING,
                "url": reverse("bo_insurer_coverage_detail", args=[r.pk]),
                "status_label": r.get_status_display(),
            })
        items.sort(key=lambda i: i["created_at"])
        for i in items:
            i["age"] = _ago(i["created_at"])
        pending_claims = self.claims.filter(status=InsuranceClaim.Status.PENDING)
        pending_pcs = self.pcs.filter(status__in=PC_PENDING)
        return {
            "items": items[:limit],
            "pending": pending_claims.count() + pending_pcs.count(),
            "info": self.pcs.filter(status__in=PC_INFO).count(),
            "late": pending_claims.filter(created_at__lt=sla).count() + pending_pcs.filter(created_at__lt=sla).count(),
        }

    # ── Classements ────────────────────────────────────────────────────
    def top_pharmacies(self, limit=5):
        a, b = self.win.start, self.win.end
        totals = defaultdict(lambda: {"amount": 0, "count": 0, "name": "", "id": None})
        rows = (
            self.claims.filter(created_at__gte=a, created_at__lt=b, status__in=CLAIM_ACCEPTED)
            .values("order__pharmacy_id", "order__pharmacy__name").annotate(s=Sum("amount"), n=Count("id"))
        )
        for r in rows:
            t = totals[r["order__pharmacy_id"]]
            t.update(name=r["order__pharmacy__name"], id=r["order__pharmacy_id"])
            t["amount"] += r["s"] or 0
            t["count"] += r["n"]
        rows = (
            self.pcs.filter(created_at__gte=a, created_at__lt=b, status__in=PC_ACCEPTED)
            .values("pharmacy_id", "pharmacy__name").annotate(s=Sum("insurance_amount"), n=Count("id"))
        )
        for r in rows:
            t = totals[r["pharmacy_id"]]
            t.update(name=r["pharmacy__name"], id=r["pharmacy_id"])
            t["amount"] += r["s"] or 0
            t["count"] += r["n"]
        rows = (
            self.counter.filter(created_at__gte=a, created_at__lt=b)
            .values("pharmacy_id", "pharmacy__name").annotate(s=Sum("insurance_amount"), n=Count("id"))
        )
        for r in rows:
            t = totals[r["pharmacy_id"]]
            t.update(name=r["pharmacy__name"], id=r["pharmacy_id"])
            t["amount"] += r["s"] or 0
            t["count"] += r["n"]
        ranked = sorted((t for k, t in totals.items() if k), key=lambda t: -t["amount"])[:limit]
        top = ranked[0]["amount"] if ranked else 0
        for t in ranked:
            t["share"] = _pct(t["amount"], top)
            t["avg"] = round(t["amount"] / t["count"]) if t["count"] else 0
        return ranked

    def top_medicines(self, limit=5):
        """Médicaments au coût brut le plus élevé dans les dossiers acceptés."""
        a, b = self.win.start, self.win.end
        totals = defaultdict(lambda: {"amount": 0, "qty": 0})
        order_ids = self.claims.filter(
            created_at__gte=a, created_at__lt=b, status__in=CLAIM_ACCEPTED
        ).values_list("order_id", flat=True)
        for r in OrderItem.objects.filter(order_id__in=order_ids).values("medicine_name").annotate(
            s=Sum(F("unit_price") * F("quantity")), q=Sum("quantity")
        ):
            totals[r["medicine_name"]]["amount"] += r["s"] or 0
            totals[r["medicine_name"]]["qty"] += r["q"] or 0
        for r in CoverageInvoiceLine.objects.filter(
            invoice__coverage_request__in=self.pcs.filter(created_at__gte=a, created_at__lt=b, status__in=PC_ACCEPTED),
            is_covered=True,
        ).values("name").annotate(s=Sum("line_total"), q=Sum("quantity")):
            totals[r["name"]]["amount"] += r["s"] or 0
            totals[r["name"]]["qty"] += r["q"] or 0
        for r in CounterSaleItem.objects.filter(
            sale__in=self.counter.filter(created_at__gte=a, created_at__lt=b)
        ).values("medicine_name").annotate(s=Sum(F("unit_price") * F("quantity")), q=Sum("quantity")):
            totals[r["medicine_name"]]["amount"] += r["s"] or 0
            totals[r["medicine_name"]]["qty"] += r["q"] or 0
        ranked = sorted(
            ({"name": k, **v} for k, v in totals.items() if k), key=lambda t: -t["amount"]
        )[:limit]
        top = ranked[0]["amount"] if ranked else 0
        for t in ranked:
            t["share"] = _pct(t["amount"], top)
        return ranked

    # ── Alertes réelles ────────────────────────────────────────────────
    def alerts(self, queue) -> list[dict]:
        out = []
        pid = self.provider.pk if self.provider else None
        fraud = InsurerFraudAlert.objects.filter(insurance_provider_id=pid).exclude(
            status=InsurerFraudAlert.Status.RESOLVED
        )
        severe = fraud.filter(level__in=[InsurerFraudAlert.Level.CRITICAL, InsurerFraudAlert.Level.HIGH])
        if severe.exists():
            latest = severe.order_by("-detected_at").first()
            out.append({
                "tone": "red", "icon": "gpp_bad",
                "text": f"{severe.count()} alerte(s) fraude critique(s) ou élevée(s) ouvertes",
                "meta": f"Dernière : {latest.holder_name} · {_ago(latest.detected_at)}",
                "url": reverse("bo_insurer_fraud"),
            })
        if queue["late"]:
            out.append({
                "tone": "amber", "icon": "timer_off",
                "text": f"{queue['late']} dossier(s) en attente depuis plus de {SLA_HOURS} h",
                "meta": "Délai de traitement dépassé — risque d'insatisfaction assurés et pharmacies",
                "url": reverse("bo_insurer_claims_pending"),
            })
        if queue["info"]:
            out.append({
                "tone": "blue", "icon": "forum",
                "text": f"{queue['info']} dossier(s) en attente d'informations du patient",
                "meta": "Relancez si besoin depuis la fiche du dossier",
                "url": reverse("bo_insurer_coverage_list"),
            })
        due = self.statements.filter(
            status__in=[InsuranceStatement.Status.SENT, InsuranceStatement.Status.PARTIAL]
        )
        if due.exists():
            balance = (_sum(due, "total") - _sum(due, "paid_amount"))
            out.append({
                "tone": "violet", "icon": "request_quote",
                "text": f"{due.count()} relevé(s) de tiers payant reçus des pharmacies",
                "meta": f"{fmt_int(balance)} FCFA à régler",
                "url": reverse("bo_insurer_payments"),
            })
        near_cap = self._members_near_cap()
        if near_cap:
            out.append({
                "tone": "amber", "icon": "data_thresholding",
                "text": f"{near_cap} assuré(s) à plus de 90 % de leur plafond annuel",
                "meta": f"Plafond {fmt_int(self.provider.annual_cap)} FCFA / an",
                "url": reverse("bo_insurer_members"),
            })
        partner = getattr(self.profile, "_partner", None)
        if partner:
            sub = partner.platform_subscriptions.filter(status="active").order_by("-ends_at").first()
            if sub and sub.ends_at and (sub.ends_at - timezone.localdate()).days <= 15:
                out.append({
                    "tone": "red", "icon": "event_busy",
                    "text": f"Votre abonnement Gab'Pharma expire le {sub.ends_at:%d/%m/%Y}",
                    "meta": "Renouvelez pour garder l'accès au portail",
                    "url": reverse("bo_insurer_subscription"),
                })
        return out

    def _members_near_cap(self) -> int:
        if not self.provider or not self.provider.annual_cap:
            return 0
        year_start = timezone.make_aware(datetime(timezone.localdate().year, 1, 1))
        per_client = defaultdict(int)
        for r in self.claims.filter(created_at__gte=year_start, status__in=CLAIM_ACCEPTED).values("client_id").annotate(s=Sum("amount")):
            per_client[r["client_id"]] += r["s"] or 0
        for r in self.pcs.filter(created_at__gte=year_start, status__in=PC_ACCEPTED).values("client_id").annotate(s=Sum("insurance_amount")):
            per_client[r["client_id"]] += r["s"] or 0
        threshold = self.provider.annual_cap * 0.9
        return sum(1 for v in per_client.values() if v >= threshold)

    # ── Mise en route (compte non validé / organisme non rattaché) ────
    def onboarding(self) -> list[dict] | None:
        partner = getattr(self.profile, "_partner", None)
        validated = bool(getattr(self.profile, "validated_at", None))
        has_provider = bool(self.provider)
        has_sub = bool(partner and partner.platform_subscriptions.filter(status="active").exists())
        has_plans = bool(has_provider and InsuranceSubscriptionPlan.objects.filter(provider=self.provider).exists())
        has_members = bool(has_provider and self.members_count() > 0)
        steps = [
            {"label": "Compte créé", "done": True, "hint": "Votre espace assureur existe."},
            {"label": "Validation par Gab'Pharma", "done": validated,
             "hint": "Vérification de vos documents (agrément, RCCM, NIF) — 24 à 72 h ouvrées."},
            {"label": "Organisme rattaché", "done": has_provider,
             "hint": "Votre compte est relié à votre compagnie pour recevoir ses dossiers."},
            {"label": "Abonnement plateforme actif", "done": has_sub or getattr(self.profile, "preview", False),
             "hint": "Débloque la gestion des dossiers et les relevés pharmacies.",
             "url": reverse("bo_insurer_subscription")},
            {"label": "Formules de garanties publiées", "done": has_plans,
             "hint": "Vos formules (taux, plafonds) proposées aux assurés.",
             "url": reverse("bo_insurer_contracts")},
            {"label": "Premiers assurés rattachés", "done": has_members,
             "hint": "Invitez vos assurés à renseigner leur n° de carte dans Gab'Pharma.",
             "url": reverse("bo_insurer_members")},
        ]
        if all(s["done"] for s in steps):
            return None
        return steps

    def members_count(self, until=None) -> int:
        if not self.provider:
            return 0
        profiles = ClientProfile.objects.filter(insurance_provider=self.provider)
        subs = InsuranceMemberSubscription.objects.filter(
            insurance_provider=self.provider, status=InsuranceMemberSubscription.Status.ACTIVE
        )
        if until is not None:
            profiles = profiles.filter(user__date_joined__lt=until)
            subs = subs.filter(created_at__lt=until)
        ids = set(profiles.values_list("user_id", flat=True)) | set(subs.values_list("user_id", flat=True))
        return len(ids)

    # ── Assemblage ─────────────────────────────────────────────────────
    def build(self) -> dict:
        w = self.win
        received = self._received(w.start, w.end)
        received_prev = self._received(w.prev_start, w.start)
        accepted = self._accepted_count(w.start, w.end)
        rejected = self._rejected_count(w.start, w.end)
        decided = accepted + rejected
        accept_rate = _pct(accepted, decided)
        accept_rate_prev = _pct(self._accepted_count(w.prev_start, w.start), self._accepted_count(w.prev_start, w.start) + self._rejected_count(w.prev_start, w.start))
        engaged = self._engaged(w.start, w.end)
        engaged_prev = self._engaged(w.prev_start, w.start)
        delay = self._decision_hours(w.start, w.end)
        delay_prev = self._decision_hours(w.prev_start, w.start)
        queue = self.work_queue()

        statements_due = self.statements.filter(
            status__in=[InsuranceStatement.Status.SENT, InsuranceStatement.Status.PARTIAL]
        )
        owed_claims = _sum(self.claims.filter(status=InsuranceClaim.Status.APPROVED), "amount")
        owed = owed_claims + _sum(statements_due, "total") - _sum(statements_due, "paid_amount")

        def delay_trend():
            if delay is None or delay_prev is None:
                return None
            t = _trend(delay, delay_prev)
            if t and t["direction"] in {"up", "down"}:
                # Un délai qui baisse est une bonne nouvelle
                t["good"] = t["direction"] == "down"
            return t

        def rate_trend():
            if not decided:
                return None
            diff = accept_rate - accept_rate_prev
            if abs(diff) < 0.05:
                return {"label": "Stable", "direction": "flat"}
            return {"label": f"{'+' if diff > 0 else '−'}{abs(diff):.1f} pt".replace(".", ","),
                    "direction": "up" if diff > 0 else "down"}

        kpis = [
            {"label": "Dossiers reçus", "value": fmt_int(received), "icon": "inbox", "tone": "purple",
             "trend": _trend(received, received_prev), "hint": w.label,
             "url": reverse("bo_insurer_claims")},
            {"label": "À traiter", "value": fmt_int(queue["pending"]), "icon": "pending_actions",
             "tone": "amber" if queue["pending"] else "green",
             "hint": f"{queue['late']} hors délai ({SLA_HOURS} h)" if queue["late"] else "Aucun retard",
             "url": reverse("bo_insurer_claims_pending")},
            {"label": "Taux d'acceptation", "value": f"{accept_rate:.1f} %".replace(".", ",") if decided else "—",
             "icon": "verified", "tone": "green", "trend": rate_trend(),
             "hint": f"{fmt_int(accepted)} acceptés · {fmt_int(rejected)} refusés"},
            {"label": "Délai moyen de décision",
             "value": (f"{delay:.0f} h" if delay >= 1 else "< 1 h") if delay is not None else "—",
             "icon": "timer", "tone": "blue", "trend": delay_trend(),
             "hint": f"Objectif : moins de {SLA_HOURS} h"},
            {"label": "Montant engagé", "value": f"{fmt_int(engaged['total'])} F", "icon": "payments",
             "tone": "purple", "trend": _trend(engaged["total"], engaged_prev["total"]),
             "hint": f"Coût moyen : {fmt_int(engaged['total'] / accepted) if accepted else 0} F / dossier"},
            {"label": "À payer aux pharmacies", "value": f"{fmt_int(owed)} F", "icon": "account_balance",
             "tone": "red" if owed else "green",
             "hint": f"{statements_due.count()} relevé(s) reçu(s)", "url": reverse("bo_insurer_payments")},
        ]

        # Graphiques
        evo_labels, evo_received, evo_accepted, amount_data = [], [], [], []
        for a, b, label in _buckets(w):
            evo_labels.append(label)
            evo_received.append(self._received(a, b))
            evo_accepted.append(self._accepted_count(a, b))
            amount_data.append(self._engaged(a, b)["total"])

        pending_now = queue["pending"]
        status_data = [accepted, pending_now, queue["info"], rejected]

        recent = self._recent_activity()
        members = self.members_count()
        members_prev = self.members_count(until=w.start)
        pharmacies_active = len({p["id"] for p in self.top_pharmacies(limit=1000)})

        return {
            "period": w.key,
            "period_label": w.label,
            "periods": [(k, v[1]) for k, v in PERIODS.items()],
            "has_provider": bool(self.provider),
            "provider": self.provider,
            "onboarding": self.onboarding(),
            "kpis": kpis,
            "queue": queue,
            "alerts": self.alerts(queue),
            "top_pharmacies": self.top_pharmacies(),
            "top_medicines": self.top_medicines(),
            "recent": recent,
            "engaged": engaged,
            "engaged_split": [
                {"label": "Commandes Gab'Pharma", "value": engaged["claims"], "color": "#5B47FB"},
                {"label": "Prises en charge (PC)", "value": engaged["pcs"], "color": "#0EA5E9"},
                {"label": "Tiers payant comptoir", "value": engaged["counter"], "color": "#10B981"},
            ],
            "status_total": sum(status_data),
            "footer_stats": [
                {"label": "Assurés rattachés", "value": fmt_int(members),
                 "trend": f"+{fmt_int(members - members_prev)} sur la période" if members > members_prev else "Pas de nouvel assuré"},
                {"label": "Pharmacies actives", "value": fmt_int(pharmacies_active),
                 "trend": f"ont servi vos assurés · {w.label}"},
                {"label": "Taux de couverture", "value": f"{self.provider.coverage_rate} %" if self.provider else "—",
                 "trend": f"Plafond annuel {fmt_int(self.provider.annual_cap)} F" if self.provider else ""},
                {"label": "Dossiers refusés", "value": f"{_pct(rejected, decided):.1f} %".replace(".", ",") if decided else "—",
                 "trend": f"{fmt_int(rejected)} sur {fmt_int(decided)} décidés"},
            ],
            "charts": {
                "status": {"labels": ["Acceptés", "En attente", "Infos demandées", "Refusés"],
                           "data": status_data, "colors": ["#10B981", "#F59E0B", "#0EA5E9", "#EF4444"]},
                "evolution": {"labels": evo_labels, "received": evo_received, "accepted": evo_accepted},
                "amounts": {"labels": evo_labels, "data": amount_data},
                "split": {"labels": ["Commandes", "Prises en charge", "Comptoir"],
                          "data": [engaged["claims"], engaged["pcs"], engaged["counter"]],
                          "colors": ["#5B47FB", "#0EA5E9", "#10B981"]},
            },
            "notif_count": pending_now,
            "display_date": f"{timezone.localdate().day} {_MONTHS_LONG[timezone.localdate().month - 1]} {timezone.localdate().year}",
            "is_demo": False,
        }

    def _recent_activity(self, limit=6):
        rows = []
        for c in self.claims.select_related("client", "order", "order__pharmacy").order_by("-created_at")[:limit]:
            rows.append({
                "kind": "Commande", "ref": c.order.code if c.order_id else f"DP-{c.pk}",
                "client_name": c.client.get_full_name() or c.client.username,
                "pharmacy": c.order.pharmacy.name if c.order_id and c.order.pharmacy_id else "—",
                "amount": c.amount, "status": c.status, "status_label": c.get_status_display(),
                "created_at": timezone.localtime(c.created_at),
                "url": reverse("bo_insurer_claim_detail", args=[c.pk]),
            })
        tone = {**{s: "approved" for s in PC_ACCEPTED}, **{s: "pending" for s in PC_PENDING | PC_INFO},
                CoverageRequest.Status.REJECTED: "rejected"}
        for r in self.pcs.select_related("client", "pharmacy").order_by("-created_at")[:limit]:
            rows.append({
                "kind": "Prise en charge", "ref": r.reference,
                "client_name": r.client.get_full_name() or r.client.username,
                "pharmacy": r.pharmacy.name if r.pharmacy_id else "—",
                "amount": r.insurance_amount or r.estimated_amount, "status": tone.get(r.status, "pending"),
                "status_label": r.get_status_display(), "created_at": timezone.localtime(r.created_at),
                "url": reverse("bo_insurer_coverage_detail", args=[r.pk]),
            })
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]


def dashboard_charts_json(widgets: dict) -> str:
    return json.dumps(widgets["charts"])
