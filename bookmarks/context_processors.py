from bookmarks import utils
from bookmarks.models import BookmarkSavedSearch, Toast


def toasts(request):
    user = request.user
    toast_messages = (
        Toast.objects.filter(owner=user, acknowledged=False)
        if user.is_authenticated
        else []
    )
    has_toasts = len(toast_messages) > 0

    return {
        "has_toasts": has_toasts,
        "toast_messages": toast_messages,
    }


def saved_searches(request):
    user = request.user
    items = (
        BookmarkSavedSearch.objects.filter(owner=user).order_by("name")
        if user.is_authenticated
        else []
    )
    return {"saved_searches": items}


def app_version(request):
    return {"app_version": utils.app_version}
