"""Alternatives hub and per-competitor comparison pages.

Data lives in the top-level alternatives.py module; this blueprint wires
the URL patterns to template dispatch."""

from flask import Blueprint, jsonify, render_template

alternatives_bp = Blueprint("alternatives", __name__)


@alternatives_bp.route("/alternatives")
def alternatives_hub():
    """Hub page listing every competitor we have an alternatives page for.
    Internal links from here distribute SEO authority to the per-competitor
    pages — that's the actual ranking-target wedge for a new domain."""
    from alternatives import COMPETITORS
    return render_template("alternatives_hub.html", competitors=COMPETITORS)


@alternatives_bp.route("/alternatives/<slug>")
def alternative_page(slug):
    """Per-competitor alternative page (e.g. /alternatives/submagic).
    Data lives in alternatives.py; the template is shared but each entry
    ships unique copy so Google doesn't penalize as thin content."""
    from alternatives import COMPETITORS_BY_SLUG
    competitor = COMPETITORS_BY_SLUG.get(slug)
    if not competitor:
        return jsonify({"ok": False, "error": "Unknown competitor"}), 404
    return render_template("alternative.html", competitor=competitor)
