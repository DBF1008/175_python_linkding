import urllib.parse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from bookmarks.forms import BookmarkSavedSearchForm
from bookmarks.models import BookmarkSavedSearch, BookmarkSearch
from bookmarks.utils import get_safe_return_url
from bookmarks.views import access


@login_required
def index(request: HttpRequest):
    saved_searches = BookmarkSavedSearch.objects.filter(owner=request.user).order_by(
        "name"
    )
    context = {"saved_searches": saved_searches}
    return render(request, "saved_searches/index.html", context)


def _capture_query(request: HttpRequest) -> str:
    # Capture the current search relative to the user's preferences, mirroring how
    # the bookmark pages build their displayed list (see views/bookmarks.py). This
    # keeps a saved search reproducing the exact view it was created from, and stays
    # page-agnostic so it can be applied on both the bookmarks and archived pages.
    search = BookmarkSearch.from_request(
        request, request.POST, request.user_profile.search_preferences
    )
    return urllib.parse.urlencode(search.query_params)


@login_required
def action(request: HttpRequest):
    fallback_url = reverse("linkding:bookmarks.index")

    if "create" in request.POST:
        form = BookmarkSavedSearchForm(request.POST)
        if not form.is_valid():
            return HttpResponse(status=422)
        saved_search = form.save(commit=False)
        saved_search.owner = request.user
        saved_search.query = _capture_query(request)
        saved_search.save()
        messages.success(request, "Saved search created successfully.")
        return_url = get_safe_return_url(request.POST.get("return_url"), fallback_url)
        return HttpResponseRedirect(return_url)

    if "overwrite" in request.POST:
        saved_search = access.saved_search_write(request, request.POST.get("overwrite"))
        saved_search.query = _capture_query(request)
        saved_search.save()
        messages.success(
            request, f"Saved search '{saved_search.name}' updated successfully."
        )
        return_url = get_safe_return_url(request.POST.get("return_url"), fallback_url)
        return HttpResponseRedirect(return_url)

    if "rename" in request.POST:
        saved_search = access.saved_search_write(request, request.POST.get("rename"))
        form = BookmarkSavedSearchForm(request.POST, instance=saved_search)
        if not form.is_valid():
            return HttpResponse(status=422)
        form.save()
        messages.success(request, "Saved search renamed successfully.")
        return HttpResponseRedirect(reverse("linkding:saved_searches.index"))

    if "remove" in request.POST:
        saved_search = access.saved_search_write(request, request.POST.get("remove"))
        saved_search_name = saved_search.name
        saved_search.delete()
        messages.success(
            request, f"Saved search '{saved_search_name}' removed successfully."
        )
        return HttpResponseRedirect(reverse("linkding:saved_searches.index"))

    return HttpResponseRedirect(reverse("linkding:saved_searches.index"))
