import gzip
import json
import logging
from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth.models import User
from django.utils import timezone

from bookmarks.forms import UserProfileForm
from bookmarks.models import Bookmark, BookmarkBundle, Tag
from bookmarks.services import tasks
from bookmarks.services.importer import TagCache, _get_batches
from bookmarks.utils import normalize_url

logger = logging.getLogger(__name__)

# Profile fields that can be restored from backup
RESTORABLE_PROFILE_FIELDS = set(UserProfileForm.Meta.fields)


@dataclass
class RestoreOptions:
    mode: str = "merge"  # "merge" or "replace"


@dataclass
class RestoreResult:
    bookmarks_created: int = 0
    bookmarks_updated: int = 0
    bookmarks_failed: int = 0
    tags_created: int = 0
    bundles_created: int = 0
    bundles_updated: int = 0
    profile_updated: bool = False


def restore_backup(
    data: bytes, user: User, options: RestoreOptions | None = None
) -> RestoreResult:
    """
    Restore workspace data from a gzipped JSON backup.

    Args:
        data: Gzipped JSON bytes from create_backup().
        user: The user to restore data for.
        options: RestoreOptions with mode="merge" (default) or "replace".

    Returns:
        RestoreResult with counts of created/updated/failed items.

    Raises:
        ValueError: If the backup file is invalid or unsupported.
    """
    if options is None:
        options = RestoreOptions()

    backup = _parse_backup(data)

    result = RestoreResult()

    if options.mode == "replace":
        _clear_user_data(user)

    _restore_tags(backup.get("tags", []), user, result)
    _restore_bookmarks(backup.get("bookmarks", []), user, result)
    _restore_bundles(backup.get("bundles", []), user, options.mode, result)
    _restore_profile(backup.get("profile", {}), user, result)

    # Schedule background tasks for newly imported bookmarks
    tasks.schedule_bookmarks_without_favicons(user)
    tasks.schedule_bookmarks_without_previews(user)

    return result


def _parse_backup(data: bytes) -> dict:
    """Decompress and parse the backup, validating format."""
    try:
        json_bytes = gzip.decompress(data)
    except Exception:
        raise ValueError("Invalid backup file: not a valid gzip archive")

    try:
        backup = json.loads(json_bytes)
    except Exception:
        raise ValueError("Invalid backup file: not valid JSON")

    if not isinstance(backup, dict):
        raise ValueError("Invalid backup file: expected a JSON object")

    version = backup.get("version")
    if version is None:
        raise ValueError("Unsupported backup format: missing version field")
    if version != 1:
        raise ValueError(f"Unsupported backup version: {version}")

    return backup


def _clear_user_data(user: User):
    """Delete all user-owned bookmarks, tags, and bundles."""
    # Delete bookmarks first (triggers post_delete for preview image cleanup)
    Bookmark.objects.filter(owner=user).delete()
    BookmarkBundle.objects.filter(owner=user).delete()
    Tag.objects.filter(owner=user).delete()


def _restore_tags(tags_data: list[dict], user: User, result: RestoreResult):
    """Create tags from backup data."""
    tag_cache = TagCache(user)
    tags_to_create = []

    for tag_data in tags_data:
        name = tag_data.get("name", "").strip()
        if not name:
            continue
        if len(name) > 64:
            logger.warning(
                f"Ignoring tag '{name}' (length {len(name)}) as it exceeds "
                f"maximum length of 64 characters"
            )
            continue

        if tag_cache.get(name):
            continue

        date_added_str = tag_data.get("date_added")
        try:
            date_added = datetime.fromisoformat(date_added_str) if date_added_str else timezone.now()
        except (ValueError, TypeError):
            date_added = timezone.now()

        tag = Tag(name=name, date_added=date_added, owner=user)
        tags_to_create.append(tag)
        tag_cache.put(tag)

    if tags_to_create:
        Tag.objects.bulk_create(tags_to_create)
        result.tags_created = len(tags_to_create)


def _restore_bookmarks(
    bookmarks_data: list[dict], user: User, result: RestoreResult
):
    """Create or update bookmarks from backup data."""
    tag_cache = TagCache(user)

    batches = _get_batches(bookmarks_data, 200)
    for batch in batches:
        _restore_bookmark_batch(batch, user, tag_cache, result)


def _restore_bookmark_batch(
    bookmarks_data: list[dict],
    user: User,
    tag_cache: TagCache,
    result: RestoreResult,
):
    """Restore a single batch of bookmarks."""
    batch_urls = [bm.get("url", "") for bm in bookmarks_data]
    existing_bookmarks = Bookmark.objects.filter(owner=user, url__in=batch_urls)

    bookmarks_to_create = []
    bookmarks_to_update = []

    for bm_data in bookmarks_data:
        url = bm_data.get("url", "")
        if not url:
            result.bookmarks_failed += 1
            continue

        try:
            bookmark = next(
                (bm for bm in existing_bookmarks if bm.url == url),
                None,
            )
            is_update = bookmark is not None
            if not bookmark:
                bookmark = Bookmark(owner=user)

            _copy_bookmark_data(bm_data, bookmark)
            bookmark.clean_fields(exclude=["owner"])

            if is_update:
                bookmarks_to_update.append(bookmark)
                result.bookmarks_updated += 1
            else:
                bookmarks_to_create.append(bookmark)
                result.bookmarks_created += 1
        except Exception:
            logger.exception(f"Error restoring bookmark: {url[:100]}")
            result.bookmarks_failed += 1

    if bookmarks_to_update:
        Bookmark.objects.bulk_update(
            bookmarks_to_update,
            [
                "url",
                "url_normalized",
                "date_added",
                "date_modified",
                "date_accessed",
                "unread",
                "shared",
                "is_archived",
                "title",
                "description",
                "notes",
                "web_archive_snapshot_url",
                "owner",
            ],
        )

    if bookmarks_to_create:
        Bookmark.objects.bulk_create(bookmarks_to_create)

    # Assign tags
    _assign_tags(bookmarks_data, user, tag_cache, batch_urls)


def _assign_tags(
    bookmarks_data: list[dict],
    user: User,
    tag_cache: TagCache,
    batch_urls: list[str],
):
    """Bulk assign tag relationships for a batch of bookmarks."""
    existing_bookmarks = Bookmark.objects.filter(owner=user, url__in=batch_urls)

    BookmarkToTagRelationShip = Bookmark.tags.through
    relationships = []

    for bm_data in bookmarks_data:
        url = bm_data.get("url", "")
        bookmark = next(
            (bm for bm in existing_bookmarks if bm.url == url),
            None,
        )
        if not bookmark:
            continue

        tag_names = bm_data.get("tag_names", [])
        tags = tag_cache.get_all(tag_names)
        for tag in tags:
            relationships.append(
                BookmarkToTagRelationShip(bookmark=bookmark, tag=tag)
            )

    if relationships:
        BookmarkToTagRelationShip.objects.bulk_create(
            relationships, ignore_conflicts=True
        )


def _copy_bookmark_data(bm_data: dict, bookmark: Bookmark):
    """Copy bookmark fields from backup dict to Bookmark model."""
    bookmark.url = bm_data["url"]
    bookmark.url_normalized = normalize_url(bookmark.url)

    bookmark.title = bm_data.get("title", "")
    bookmark.description = bm_data.get("description", "")
    bookmark.notes = bm_data.get("notes", "")
    bookmark.web_archive_snapshot_url = bm_data.get("web_archive_snapshot_url", "")

    bookmark.unread = bm_data.get("unread", False)
    bookmark.is_archived = bm_data.get("is_archived", False)
    bookmark.shared = bm_data.get("shared", False)

    date_added_str = bm_data.get("date_added")
    if date_added_str:
        try:
            bookmark.date_added = datetime.fromisoformat(date_added_str)
        except (ValueError, TypeError):
            bookmark.date_added = timezone.now()
    else:
        bookmark.date_added = timezone.now()

    date_modified_str = bm_data.get("date_modified")
    if date_modified_str:
        try:
            bookmark.date_modified = datetime.fromisoformat(date_modified_str)
        except (ValueError, TypeError):
            bookmark.date_modified = bookmark.date_added
    else:
        bookmark.date_modified = bookmark.date_added

    date_accessed_str = bm_data.get("date_accessed")
    if date_accessed_str:
        try:
            bookmark.date_accessed = datetime.fromisoformat(date_accessed_str)
        except (ValueError, TypeError):
            bookmark.date_accessed = None
    else:
        bookmark.date_accessed = None


def _restore_bundles(
    bundles_data: list[dict], user: User, mode: str, result: RestoreResult
):
    """Create or update bundles from backup data."""
    existing_bundles = {
        bundle.name: bundle
        for bundle in BookmarkBundle.objects.filter(owner=user)
    }

    for bundle_data in bundles_data:
        name = bundle_data.get("name", "").strip()
        if not name:
            continue

        existing = existing_bundles.get(name)

        if existing and mode == "merge":
            _update_bundle(existing, bundle_data)
            existing.save()
            result.bundles_updated += 1
        elif not existing:
            bundle = BookmarkBundle(owner=user)
            _update_bundle(bundle, bundle_data)
            bundle.save()
            result.bundles_created += 1


def _update_bundle(bundle: BookmarkBundle, bundle_data: dict):
    """Copy bundle fields from backup dict to BookmarkBundle model."""
    bundle.name = bundle_data.get("name", "")
    bundle.search = bundle_data.get("search", "")
    bundle.any_tags = bundle_data.get("any_tags", "")
    bundle.all_tags = bundle_data.get("all_tags", "")
    bundle.excluded_tags = bundle_data.get("excluded_tags", "")

    filter_unread = bundle_data.get("filter_unread", "off")
    if filter_unread in ("off", "yes", "no"):
        bundle.filter_unread = filter_unread

    filter_shared = bundle_data.get("filter_shared", "off")
    if filter_shared in ("off", "yes", "no"):
        bundle.filter_shared = filter_shared

    order = bundle_data.get("order")
    if isinstance(order, int):
        bundle.order = order


def _restore_profile(
    profile_data: dict, user: User, result: RestoreResult
):
    """Restore user profile settings from backup data."""
    if not profile_data:
        return

    profile = user.profile
    updated = False

    for field_name, value in profile_data.items():
        if field_name not in RESTORABLE_PROFILE_FIELDS:
            continue

        current_value = getattr(profile, field_name, None)
        if current_value != value:
            setattr(profile, field_name, value)
            updated = True

    if updated:
        profile.save()
        result.profile_updated = True
