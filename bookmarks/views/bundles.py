from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import HttpRequest, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from bookmarks.forms import BookmarkBundleForm
from bookmarks.models import BookmarkBundle, BookmarkSearch
from bookmarks.queries import parse_query_string
from bookmarks.services import bundles
from bookmarks.views import access
from bookmarks.views.contexts import ActiveBookmarkListContext


@login_required
def index(request: HttpRequest):
    bundles = BookmarkBundle.objects.filter(owner=request.user).order_by("order")
    context = {"bundles": bundles}
    return render(request, "bundles/index.html", context)


@login_required
def action(request: HttpRequest):
    if "remove_bundle" in request.POST:
        remove_bundle_id = request.POST.get("remove_bundle")
        bundle = access.bundle_write(request, remove_bundle_id)
        bundle_name = bundle.name
        bundles.delete_bundle(bundle)
        messages.success(request, f"Bundle '{bundle_name}' removed successfully.")

    elif "move_bundle" in request.POST:
        bundle_id = request.POST.get("move_bundle")
        bundle_to_move = access.bundle_write(request, bundle_id)
        move_position = int(request.POST.get("move_position"))
        bundles.move_bundle(bundle_to_move, move_position)

    return HttpResponseRedirect(reverse("linkding:bundles.index"))


def _handle_edit(request: HttpRequest, template: str, bundle: BookmarkBundle = None):
    form_data = request.POST if request.method == "POST" else None
    initial_data = {}
    if bundle is None and request.method == "GET":
        query_param = request.GET.get("q")
        if query_param:
            parsed = parse_query_string(query_param)

            if parsed["search_terms"]:
                initial_data["search"] = " ".join(parsed["search_terms"])
            if parsed["tag_names"]:
                initial_data["all_tags"] = " ".join(parsed["tag_names"])

    form = BookmarkBundleForm(form_data, instance=bundle, initial=initial_data)

    if request.method == "POST" and form.is_valid():
        instance = form.save(commit=False)

        if bundle is None:
            instance.order = None
            bundles.create_bundle(instance, request.user)
        else:
            instance.save()

        messages.success(request, "Bundle saved successfully.")
        return HttpResponseRedirect(reverse("linkding:bundles.index"))

    status = 422 if request.method == "POST" and not form.is_valid() else 200
    bookmark_list = _get_bookmark_list_preview(request, bundle, initial_data)
    context = {
        "form": form,
        "bundle": bundle,
        "bookmark_list": bookmark_list,
    }

    return render(request, template, context, status=status)


@login_required
def new(request: HttpRequest):
    return _handle_edit(request, "bundles/new.html")


@login_required
def edit(request: HttpRequest, bundle_id: int):
    bundle = access.bundle_write(request, bundle_id)

    return _handle_edit(request, "bundles/edit.html", bundle)


@login_required
def preview(request: HttpRequest):
    bookmark_list = _get_bookmark_list_preview(request)
    context = {"bookmark_list": bookmark_list}
    return render(request, "bundles/preview.html", context)


def _get_bookmark_list_preview(
    request: HttpRequest,
    bundle: BookmarkBundle | None = None,
    initial_data: dict = None,
):
    if request.method == "GET" and bundle:
        preview_bundle = bundle
    else:
        form_data = (
            request.POST.copy() if request.method == "POST" else request.GET.copy()
        )
        if initial_data:
            for key, value in initial_data.items():
                form_data[key] = value

        form_data["name"] = "Preview Bundle"  # Set dummy name for form validation
        form = BookmarkBundleForm(form_data)
        preview_bundle = form.save(commit=False)

    search = BookmarkSearch(bundle=preview_bundle)
    bookmark_list = ActiveBookmarkListContext(request, search)
    bookmark_list.is_preview = True
    return bookmark_list


def _map_filter_value(value: str, default: str = BookmarkBundle.FILTER_STATE_OFF) -> str:
    """Map BookmarkSearch filter values to BookmarkBundle filter values."""
    if value in (BookmarkBundle.FILTER_STATE_OFF, BookmarkBundle.FILTER_STATE_YES, BookmarkBundle.FILTER_STATE_NO):
        return value
    return default


@login_required
def quick_save(request: HttpRequest):
    """Quick save current search as a named filter (bundle).

    This view allows users to save their current search state directly from
    the search preferences dropdown without navigating to the bundles page.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("Only POST requests are allowed")

    name = request.POST.get("name", "").strip()
    bundle_id = request.POST.get("save_filter_bundle_id")
    return_url = request.POST.get("return_url") or reverse("linkding:bookmarks.index")

    if not name:
        messages.error(request, "Filter name is required")
        return HttpResponseRedirect(return_url)

    # Parse current search state from form
    q = request.POST.get("q", "")
    parsed = parse_query_string(q)
    search_terms = parsed.get("search_terms", [])
    tag_names = parsed.get("tag_names", [])

    # Map preferences to bundle filter values
    unread = request.POST.get("unread", BookmarkBundle.FILTER_STATE_OFF)
    shared = request.POST.get("shared", BookmarkBundle.FILTER_STATE_OFF)

    if bundle_id:
        # Update existing bundle
        bundle = BookmarkBundle.objects.filter(owner=request.user, pk=bundle_id).first()
        if not bundle:
            return HttpResponseNotFound("Bundle not found")
    else:
        # Create new bundle
        bundle = BookmarkBundle(owner=request.user)
        # Set order for new bundle
        max_order_result = BookmarkBundle.objects.filter(owner=request.user).aggregate(
            Max("order", default=-1)
        )
        bundle.order = max_order_result["order__max"] + 1

    # Update bundle fields
    bundle.name = name
    bundle.search = " ".join(search_terms)
    bundle.all_tags = " ".join(tag_names)
    bundle.filter_unread = _map_filter_value(unread)
    bundle.filter_shared = _map_filter_value(shared)

    bundle.save()

    messages.success(request, f"Filter '{name}' saved successfully")

    # Redirect with bundle param to apply the filter
    separator = "&" if "?" in return_url else "?"
    url = f"{return_url}{separator}bundle={bundle.id}"
    return HttpResponseRedirect(url)
