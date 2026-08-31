"""Documents officiels requis pour une pharmacie partenaire."""

PHARMACY_REQUIRED_DOCUMENTS = [
    {
        "key": "rccm",
        "title": "RCCM",
        "label": "Registre de Commerce (RCCM)",
        "description": "Certificat d’immatriculation au registre du commerce de l’officine.",
        "keywords": ("rccm", "registre", "commerce"),
        "expires": True,
    },
    {
        "key": "nif",
        "title": "NIF",
        "label": "Numéro d’identification fiscale (NIF)",
        "description": "Attestation fiscale ou carte NIF de l’établissement.",
        "keywords": ("nif", "fiscal", "impôt", "impot"),
        "expires": False,
    },
    {
        "key": "agrement",
        "title": "Agrément d’exploitation",
        "label": "Agrément / autorisation d’ouverture",
        "description": "Autorisation ministérielle ou préfectorale d’exploitation de la pharmacie.",
        "keywords": ("agrément", "agrement", "autorisation", "exploitation", "ouverture"),
        "expires": True,
    },
    {
        "key": "ordre",
        "title": "Inscription à l’Ordre",
        "label": "Inscription du pharmacien titulaire",
        "description": "Attestation d’inscription à l’Ordre national des pharmaciens du Gabon.",
        "keywords": ("ordre", "pharmacien", "titulaire", "rpps", "inscription"),
        "expires": True,
    },
    {
        "key": "id",
        "title": "Pièce d’identité du responsable",
        "label": "Pièce d’identité du gérant",
        "description": "CNI ou passeport en cours de validité du pharmacien responsable.",
        "keywords": ("cni", "identité", "identite", "passeport", "pièce", "piece"),
        "expires": True,
    },
]


def _match_document(doc, keywords):
    title = (doc.title or "").lower()
    return any(kw in title for kw in keywords)


def pharmacy_document_checklist(pharmacy):
    """État de conformité des pièces obligatoires."""
    if not pharmacy:
        return []
    docs = list(pharmacy.documents.all())
    used_ids = set()
    rows = []
    for req in PHARMACY_REQUIRED_DOCUMENTS:
        matched = None
        for doc in docs:
            if doc.id in used_ids:
                continue
            if _match_document(doc, req["keywords"]):
                matched = doc
                used_ids.add(doc.id)
                break
        if matched:
            if matched.is_expired:
                status = "expired"
                status_label = "Expiré — à renouveler"
            elif matched.is_expiring_soon:
                status = "expiring"
                status_label = "Expire bientôt"
            else:
                status = "ok"
                status_label = "À jour"
        else:
            status = "missing"
            status_label = "À téléverser"
        rows.append(
            {
                **req,
                "doc": matched,
                "status": status,
                "status_label": status_label,
            }
        )
    extra = [d for d in docs if d.id not in used_ids]
    return rows, extra


def pharmacy_compliance_summary(pharmacy):
    rows, _extra = pharmacy_document_checklist(pharmacy)
    if not rows:
        return {"complete": 0, "total": 0, "pct": 0, "missing": 0, "alerts": 0}
    ok = sum(1 for r in rows if r["status"] == "ok")
    alerts = sum(1 for r in rows if r["status"] in {"expiring", "expired"})
    missing = sum(1 for r in rows if r["status"] == "missing")
    total = len(rows)
    return {
        "complete": ok,
        "total": total,
        "pct": int((ok / total) * 100) if total else 0,
        "missing": missing,
        "alerts": alerts,
    }
