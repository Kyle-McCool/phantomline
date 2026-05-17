"""Blog index and per-article routes.

Data lives in the top-level blog.py module; this blueprint just wires
the two URL patterns to template dispatch."""

from flask import Blueprint, jsonify, redirect, render_template

blog_bp = Blueprint("blog", __name__)


# Posts that have been retired or consolidated into a stronger pillar.
# Keeps the URL valid (no 404) and forwards Google's ranking signal to
# the canonical page. Each entry: old_slug -> new_path.
_BLOG_REDIRECTS = {
    # Cannibalized "best faceless youtube niches" coverage — pillar page
    # is stronger and ranks for the same query. 301 to consolidate.
    "best-faceless-youtube-niches-passive-income": "/faceless-youtube-niches",
}


@blog_bp.route("/blog")
@blog_bp.route("/blog/")
def blog_index():
    """Blog landing — lists published articles newest first."""
    from blog import published_articles
    return render_template("blog_index.html", articles=published_articles())


@blog_bp.route("/blog/<slug>")
def blog_article(slug):
    """Per-article blog post. Each post has its own template at
    templates/blog_<slug>.html so the per-post schema and meta tags can be
    custom without having to template them through a shared layer."""
    redirect_target = _BLOG_REDIRECTS.get(slug)
    if redirect_target:
        return redirect(redirect_target, code=301)
    from blog import ARTICLES_BY_SLUG
    article = ARTICLES_BY_SLUG.get(slug)
    if not article or not article.get("published"):
        return jsonify({"ok": False, "error": "Article not found"}), 404
    template_name = f"blog_{slug}.html"
    return render_template(template_name, article=article)
