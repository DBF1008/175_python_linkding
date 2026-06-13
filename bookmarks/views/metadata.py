import urllib.parse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse

from bookmarks import metadata, utils
from bookmarks.metadata import get_metadata_states, get_status_filter
from bookmarks.models import Bookmark
from bookmarks.services.bookmarks import retry_bookmarks_metadata
from bookmarks.type_defs import HttpRequest


class MetadataItem:
    def __init__(self, bookmark: Bookmark, states):
        self.bookmark = bookmark
        self.id = bookmark.id
        self.url = bookmark.url
        self.title = bookmark.resolved_title
        self.states = states


def _filtered_bookmarks(request: HttpRequest):
    profile = request.user_profile

    status = request.GET.get("status", metadata.FILTER_ALL)
    if status not in metadata.FILTER_CHOICES:
        status = metadata.FILTER_ALL
    search = request.GET.get("q", "").strip()

    query_set = Bookmark.objects.filter(owner=request.user)

    status_filter = get_status_filter(status, profile)
    if status_filter is not None:
        query_set = query_set.filter(status_filter)

    if search:
        query_set = query_set.filter(
            Q(title__icontains=search) | Q(url__icontains=search)
        )

    return query_set, status, search


@login_required
def index(request: HttpRequest):
    profile = request.user_profile
    query_set, status, search = _filtered_bookmarks(request)
    query_set = query_set.order_by("-date_added")

    paginator = Paginator(query_set, profile.items_per_page)
    page_number = request.GET.get("page")
    page = paginator.get_page(page_number)

    items = [
        MetadataItem(bookmark, get_metadata_states(bookmark, profile))
        for bookmark in page
    ]

    # Summary counts across the whole library, independent of the active filter
    owned = Bookmark.objects.filter(owner=request.user)

    def count_for(filter_name):
        status_q = get_status_filter(filter_name, profile)
        return owned.filter(status_q).count() if status_q is not None else 0

    summary = {
        "total": owned.count(),
        "attention": count_for(metadata.FILTER_ATTENTION),
        "failed": count_for(metadata.FILTER_FAILED),
        "pending": count_for(metadata.FILTER_PENDING),
    }

    # Preserve the active filter/search in action submissions and links
    base_query = {}
    if status != metadata.FILTER_ALL:
        base_query["status"] = status
    if search:
        base_query["q"] = search
    query_string = urllib.parse.urlencode(base_query)
    action_url = reverse("linkding:metadata.index.action")
    if query_string:
        action_url += "?" + query_string

    context = {
        "page_title": "Metadata - Linkding",
        "items": items,
        "page": page,
        "status": status,
        "search": search,
        "summary": summary,
        "action_url": action_url,
        "base_query": base_query,
        "filter_all": metadata.FILTER_ALL,
        "filter_attention": metadata.FILTER_ATTENTION,
        "filter_failed": metadata.FILTER_FAILED,
        "filter_pending": metadata.FILTER_PENDING,
        "metadata_features_enabled": metadata.any_metadata_feature_enabled(profile),
    }
    return render(request, "bookmarks/metadata/index.html", context)


@login_required
def action(request: HttpRequest):
    if request.method == "POST":
        if "retry" in request.POST:
            retry_bookmarks_metadata([request.POST["retry"]], request.user)
            messages.success(
                request, "Scheduled metadata collection for 1 bookmark."
            )
        elif "bulk_execute" in request.POST:
            if request.POST.get("bulk_select_across") == "on":
                query_set, _, _ = _filtered_bookmarks(request)
                bookmark_ids = list(query_set.values_list("id", flat=True))
            else:
                bookmark_ids = request.POST.getlist("bookmark_id")

            retry_bookmarks_metadata(bookmark_ids, request.user)
            messages.success(
                request,
                f"Scheduled metadata collection for {len(bookmark_ids)} bookmark(s).",
            )

    return utils.redirect_with_query(request, reverse("linkding:metadata.index"))
