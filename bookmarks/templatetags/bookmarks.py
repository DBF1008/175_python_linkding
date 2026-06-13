from django import template

from bookmarks.forms import BookmarkSearchForm
from bookmarks.models import BookmarkSearch

register = template.Library()


@register.inclusion_tag(
    "bookmarks/search.html", name="bookmark_search", takes_context=True
)
def bookmark_search(context, search: BookmarkSearch, mode: str = ""):
    search_form = BookmarkSearchForm(search, editable_fields=["q"])

    if mode == "shared":
        preferences_form = BookmarkSearchForm(search, editable_fields=["sort"])
    elif mode == "read_later":
        # The reading queue is inherently unread, so the unread filter is omitted.
        # Sort covers time-based triage and the shared filter covers status.
        preferences_form = BookmarkSearchForm(
            search, editable_fields=["sort", "shared"]
        )
    else:
        preferences_form = BookmarkSearchForm(
            search, editable_fields=["sort", "shared", "unread"]
        )

    # The search autocomplete derives its API path from the mode, and only the
    # active/archived/shared bookmark endpoints exist. The read-later queue reuses
    # the default (active) endpoint for suggestions.
    autocomplete_mode = "" if mode == "read_later" else mode

    return {
        "request": context["request"],
        "app_version": context["app_version"],
        "search": search,
        "search_form": search_form,
        "preferences_form": preferences_form,
        "mode": autocomplete_mode,
    }
