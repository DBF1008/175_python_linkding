from urllib.parse import urlencode

from django import template

from bookmarks.forms import BookmarkSearchForm
from bookmarks.models import BookmarkBundle, BookmarkSearch

register = template.Library()


@register.inclusion_tag(
    "bookmarks/search.html", name="bookmark_search", takes_context=True
)
def bookmark_search(context, search: BookmarkSearch, mode: str = ""):
    request = context["request"]
    search_form = BookmarkSearchForm(search, editable_fields=["q"])

    if mode == "shared":
        preferences_form = BookmarkSearchForm(search, editable_fields=["sort"])
    else:
        preferences_form = BookmarkSearchForm(
            search, editable_fields=["sort", "shared", "unread"]
        )

    # Get user's bundles for the save filter dropdown
    user = request.user if request.user.is_authenticated else None
    user_bundles = []
    active_bundle = None
    clear_filter_url = ""

    if user:
        user_bundles = list(
            BookmarkBundle.objects.filter(owner=user).order_by("order", "name")
        )
        if search.bundle:
            active_bundle = search.bundle
            # Build URL to clear the bundle filter while keeping other params
            params = search.query_params.copy()
            params.pop("bundle", None)
            clear_filter_url = f"?{urlencode(params)}" if params else ""

    return {
        "request": request,
        "app_version": context["app_version"],
        "search": search,
        "search_form": search_form,
        "preferences_form": preferences_form,
        "mode": mode,
        "user_bundles": user_bundles,
        "active_bundle": active_bundle,
        "clear_filter_url": clear_filter_url,
    }
