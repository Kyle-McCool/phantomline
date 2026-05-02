"""Channel insights routes — persistent SEO + analytics state.

Owns the YouTube Studio CSV ingestion paths (Traffic Sources, Search Terms)
and the title-fit scoring endpoint. Other route groups read insights via
channel_insights.load() but only this blueprint mutates them."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import channel_insights

from core import BASE_DIR, _parse_analytics_upload
from routes.billing import enforce_tier


insights_bp = Blueprint("insights", __name__)


@insights_bp.before_request
def _gate_imports_to_pro():
    """Insight ingest (CSV imports) is Pro. Reading insights and the
    title-fit scorer stays free so non-payers can evaluate the value."""
    if request.path.startswith("/api/insights/import-"):
        ok, err = enforce_tier("pro")
        if not ok:
            return jsonify({"ok": False, "error": err, "code": "UPGRADE"}), 402


@insights_bp.route("/api/insights")
def api_insights_get():
    """Return the persisted channel insights, plus a small status block."""
    insights = channel_insights.load(BASE_DIR)
    has = bool(insights and (insights.get("videos") or insights.get("seo_assets") or insights.get("search_terms")))
    return jsonify({
        "ok": True,
        "configured": has,
        "updated_at": insights.get("updated_at") if has else None,
        "insights": insights if has else {},
    })


@insights_bp.route("/api/insights/clear", methods=["POST"])
def api_insights_clear():
    channel_insights.clear(BASE_DIR)
    return jsonify({"ok": True})


@insights_bp.route("/api/insights/import-traffic", methods=["POST"])
def api_insights_import_traffic():
    """Ingest a YouTube Studio Traffic Sources CSV and merge into insights.
    Computes per-source share-percentages."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Upload the YouTube Studio Traffic Sources CSV."}), 400
    try:
        rows = _parse_analytics_upload(f)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not parse: {exc}"}), 400
    parsed = channel_insights.parse_traffic_sources_rows(rows)
    if not parsed.get("shares"):
        return jsonify({"ok": False, "error": "Could not detect a Traffic Source column. Export from YouTube Studio > Analytics > Reach > Traffic source."}), 422

    merged = channel_insights.merge(BASE_DIR, {"traffic_sources": parsed})
    return jsonify({"ok": True, "traffic_sources": parsed, "updated_at": merged.get("updated_at")})


@insights_bp.route("/api/insights/import-search", methods=["POST"])
def api_insights_import_search():
    """Ingest a YouTube Studio Search Terms CSV. Computes gap_keywords by
    cross-referencing against current video titles."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Upload the YouTube Studio Search Terms CSV."}), 400
    try:
        rows = _parse_analytics_upload(f)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not parse: {exc}"}), 400
    terms = channel_insights.parse_search_terms_rows(rows)
    if not terms:
        return jsonify({"ok": False, "error": "No search terms found. Export from YouTube Studio > Analytics > Research > Searches across YouTube."}), 422

    insights = channel_insights.load(BASE_DIR)
    titles = [v.get("title") for v in (insights.get("videos") or [])]
    gap = channel_insights.compute_gap_keywords(terms, titles)
    merged = channel_insights.merge(BASE_DIR, {"search_terms": terms, "gap_keywords": gap})
    return jsonify({"ok": True, "search_terms": terms[:25], "gap_keywords": gap, "updated_at": merged.get("updated_at")})


@insights_bp.route("/api/insights/title-fit", methods=["POST"])
def api_insights_title_fit():
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "Provide a title."}), 400
    insights = channel_insights.load(BASE_DIR)
    if not insights:
        return jsonify({"ok": True, "configured": False, "fit": {"verdict": "neutral", "score": 50, "reasons": []}})
    fit = channel_insights.title_fit(title, insights)
    return jsonify({"ok": True, "configured": True, "fit": fit, "title": title})
