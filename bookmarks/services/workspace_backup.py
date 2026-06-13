import gzip
import json

from django.contrib.auth.models import User
from django.utils import timezone

from bookmarks.forms import UserProfileForm
from bookmarks.models import Bookmark, BookmarkBundle, Tag


# Profile fields to include in backup, synced with UserProfileForm.Meta.fields
PROFILE_FIELDS = UserProfileForm.Meta.fields


def create_backup(user: User) -> bytes:
    """
    Create a complete workspace backup as gzipped JSON.

    Includes: tags, bookmarks (with full state), bundles, and user profile settings.
    Excludes: binary assets (snapshots), favicons, preview images, security tokens,
    transient data (toasts), and auto-computed fields.

    Returns gzipped JSON bytes.
    """
    data = {
        "version": 1,
        "created_at": timezone.now().isoformat(),
        "tags": _serialize_tags(user),
        "bookmarks": _serialize_bookmarks(user),
        "bundles": _serialize_bundles(user),
        "profile": _serialize_profile(user),
    }

    json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    return gzip.compress(json_bytes)


def _serialize_tags(user: User) -> list[dict]:
    tags = Tag.objects.filter(owner=user).order_by("name")
    return [
        {
            "name": tag.name,
            "date_added": tag.date_added.isoformat(),
        }
        for tag in tags
    ]


def _serialize_bookmarks(user: User) -> list[dict]:
    bookmarks = Bookmark.objects.filter(owner=user).prefetch_related("tags")
    return [
        {
            "url": bookmark.url,
            "title": bookmark.title,
            "description": bookmark.description,
            "notes": bookmark.notes,
            "web_archive_snapshot_url": bookmark.web_archive_snapshot_url,
            "unread": bookmark.unread,
            "is_archived": bookmark.is_archived,
            "shared": bookmark.shared,
            "date_added": bookmark.date_added.isoformat(),
            "date_modified": bookmark.date_modified.isoformat(),
            "date_accessed": (
                bookmark.date_accessed.isoformat()
                if bookmark.date_accessed
                else None
            ),
            "tag_names": bookmark.tag_names,
        }
        for bookmark in bookmarks
    ]


def _serialize_bundles(user: User) -> list[dict]:
    bundles = BookmarkBundle.objects.filter(owner=user).order_by("order", "name")
    return [
        {
            "name": bundle.name,
            "search": bundle.search,
            "any_tags": bundle.any_tags,
            "all_tags": bundle.all_tags,
            "excluded_tags": bundle.excluded_tags,
            "filter_unread": bundle.filter_unread,
            "filter_shared": bundle.filter_shared,
            "order": bundle.order,
        }
        for bundle in bundles
    ]


def _serialize_profile(user: User) -> dict:
    profile = user.profile
    data = {}
    for field_name in PROFILE_FIELDS:
        data[field_name] = getattr(profile, field_name)
    # Include search_preferences (JSON field) even though it's not in the form
    data["search_preferences"] = profile.search_preferences
    return data
