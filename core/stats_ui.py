"""Helpers UI pour écrans statistiques / rapports."""

ORDER_STATUS_STYLE = {
    "delivered": "green",
    "delivering": "blue",
    "ready": "amber",
    "preparing": "amber",
    "confirmed": "blue",
    "pending": "slate",
    "awaiting_rx": "amber",
    "cancelled": "rose",
    "refunded": "rose",
}


def status_cards_from_rows(status_rows, status_map):
    total = sum(r["c"] for r in status_rows) or 1
    cards = []
    for row in status_rows:
        code = row["status"]
        cards.append(
            {
                "label": status_map.get(code, code),
                "count": row["c"],
                "pct": round(100 * row["c"] / total),
                "tone": ORDER_STATUS_STYLE.get(code, "slate"),
            }
        )
    return cards
