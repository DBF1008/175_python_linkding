import json
import logging
from dataclasses import dataclass

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import prefetch_related_objects
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bookmarks import utils
from bookmarks.models import Bookmark, BookmarkBundle, Tag
from bookmarks.services import tasks
from bookmarks.services.importer import TagCache, _get_batches
from bookmarks.utils import normalize_url, unique

logger = logging.getLogger(__name__)

# Current schema version of the workspace backup format. Bump when making
# breaking changes, and keep importing older versions working.
WORKSPACE_VERSION = 1

MAX_TAG_LENGTH = 64

# Bookmark scalar fields that are part of a backup. Intentionally excludes
# derived/obsolete fields that are regenerated rather than restored:
# favicon_file, preview_image_file, latest_snapshot, website_title,
# website_description. Note this differs from the Netscape importer's update
# list, which omits is_archived/date_accessed/web_archive_snapshot_url.
BOOKMARK_UPDATE_FIELDS = [
    "url",
    "url_normalized",
    "title",
    "description",
    "notes",
    "web_archive_snapshot_url",
    "unread",
    "is_archived",
    "shared",
    "date_added",
    "date_modified",
    "date_accessed",
]


class WorkspaceImportError(Exception):
    pass


@dataclass
class WorkspaceImportResult:
    bookmarks_created: int = 0
    bookmarks_updated: int = 0
    bundles_created: int = 0
    bundles_updated: int = 0
    tags_created: int = 0


def _serialize_datetime(value):
    return value.isoformat() if value else None


def _parse_datetime(value, field_name: str):
    if value is None:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise WorkspaceImportError(f"Invalid datetime for '{field_name}': {value!r}")
    return parsed


def export_workspace(user: User) -> dict:
    """Build a complete, restorable snapshot of the user's workspace.

    Captures every bookmark field and state (including notes, unread,
    is_archived, shared), all tags (including ones not attached to any
    bookmark), and all bundles. Unlike the Netscape HTML export, this can be
    restored without losing linkding-specific data.
    """
    bookmarks = list(Bookmark.objects.filter(owner=user).order_by("id"))
    # Prefetch tags to prevent n+1 queries (matches the Netscape export view)
    prefetch_related_objects(bookmarks, "tags")
    tags = Tag.objects.filter(owner=user).order_by("name", "id")
    bundles = BookmarkBundle.objects.filter(owner=user).order_by("order", "id")

    return {
        "version": WORKSPACE_VERSION,
        "app_version": utils.app_version,
        "date_exported": timezone.now().isoformat(),
        "tags": [
            {
                "name": tag.name,
                "date_added": _serialize_datetime(tag.date_added),
            }
            for tag in tags
        ],
        "bundles": [
            {
                "name": bundle.name,
                "search": bundle.search,
                "any_tags": bundle.any_tags,
                "all_tags": bundle.all_tags,
                "excluded_tags": bundle.excluded_tags,
                "filter_unread": bundle.filter_unread,
                "filter_shared": bundle.filter_shared,
                "order": bundle.order,
                "date_created": _serialize_datetime(bundle.date_created),
                "date_modified": _serialize_datetime(bundle.date_modified),
            }
            for bundle in bundles
        ],
        "bookmarks": [
            {
                "url": bookmark.url,
                "title": bookmark.title,
                "description": bookmark.description,
                "notes": bookmark.notes,
                "unread": bookmark.unread,
                "is_archived": bookmark.is_archived,
                "shared": bookmark.shared,
                "web_archive_snapshot_url": bookmark.web_archive_snapshot_url,
                "date_added": _serialize_datetime(bookmark.date_added),
                "date_modified": _serialize_datetime(bookmark.date_modified),
                "date_accessed": _serialize_datetime(bookmark.date_accessed),
                # tag_names property returns names sorted, keeping output stable
                "tag_names": bookmark.tag_names,
            }
            for bookmark in bookmarks
        ],
    }


def export_workspace_json(user: User) -> str:
    # Serialize datetimes as ISO strings ourselves (done in export_workspace) and
    # avoid DjangoJSONEncoder, which truncates microseconds and breaks round-trips.
    return json.dumps(export_workspace(user), ensure_ascii=False, indent=2)


def import_workspace(json_str: str, user: User) -> WorkspaceImportResult:
    """Restore a workspace backup into the given user's workspace, merging.

    Bookmarks are matched by normalized URL: matches are overwritten with the
    backup's data (including their tag set), and entries without a match are
    created. Tags are matched case-insensitively by name, bundles by name.
    Data not present in the backup is left untouched. The whole restore runs in
    a single transaction and aborts on any validation error.
    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkspaceImportError("Backup file is not valid JSON.") from error

    if not isinstance(data, dict):
        raise WorkspaceImportError("Backup file has an invalid format.")

    version = data.get("version")
    if not isinstance(version, int) or version > WORKSPACE_VERSION:
        raise WorkspaceImportError(f"Unsupported backup version: {version!r}")

    tags_data = data.get("tags") or []
    bundles_data = data.get("bundles") or []
    bookmarks_data = data.get("bookmarks") or []

    result = WorkspaceImportResult()

    with transaction.atomic():
        tag_cache = _restore_tags(tags_data, bookmarks_data, user, result)
        _restore_bookmarks(bookmarks_data, user, tag_cache, result)
        _restore_bundles(bundles_data, user, result)

    # Regenerate derived assets that are intentionally not part of the backup
    tasks.schedule_bookmarks_without_favicons(user)
    tasks.schedule_bookmarks_without_previews(user)

    return result


def _restore_tags(
    tags_data: list,
    bookmarks_data: list,
    user: User,
    result: WorkspaceImportResult,
) -> TagCache:
    tag_cache = TagCache(user)

    # Preserve date_added for tags that are listed explicitly in the backup
    explicit_dates = {}
    for tag in tags_data:
        name = tag.get("name")
        if name:
            explicit_dates[name.lower()] = tag.get("date_added")

    # Collect wanted tag names from the explicit tag list and all bookmark tags
    wanted = [tag.get("name") for tag in tags_data if tag.get("name")]
    for bookmark in bookmarks_data:
        wanted.extend(bookmark.get("tag_names") or [])
    wanted = unique(wanted, str.lower)

    tags_to_create = []
    for name in wanted:
        if len(name) > MAX_TAG_LENGTH:
            logger.warning(
                f"Ignoring tag '{name}' (length {len(name)}) as it exceeds "
                f"maximum length of {MAX_TAG_LENGTH} characters"
            )
            continue
        if tag_cache.get(name):
            continue
        date_added = _parse_datetime(
            explicit_dates.get(name.lower()), "tags.date_added"
        )
        if date_added is None:
            date_added = timezone.now()
        tag = Tag(name=name, owner=user, date_added=date_added)
        tags_to_create.append(tag)
        tag_cache.put(tag)

    Tag.objects.bulk_create(tags_to_create)
    result.tags_created = len(tags_to_create)
    return tag_cache


def _restore_bookmarks(
    bookmarks_data: list,
    user: User,
    tag_cache: TagCache,
    result: WorkspaceImportResult,
):
    # De-duplicate incoming bookmarks by normalized URL (last entry wins), so a
    # hand-edited backup can't create duplicates or collide on reload-by-URL
    deduped = {}
    for entry in bookmarks_data:
        url = entry.get("url")
        if not url:
            raise WorkspaceImportError("Encountered a bookmark without a URL.")
        deduped[normalize_url(url)] = entry
    entries = list(deduped.values())

    for batch in _get_batches(entries, 200):
        _restore_bookmark_batch(batch, user, tag_cache, result)


def _restore_bookmark_batch(
    batch: list,
    user: User,
    tag_cache: TagCache,
    result: WorkspaceImportResult,
):
    batch_urls_normalized = [normalize_url(entry["url"]) for entry in batch]
    existing = {
        bookmark.url_normalized: bookmark
        for bookmark in Bookmark.objects.filter(
            owner=user, url_normalized__in=batch_urls_normalized
        )
    }

    to_create = []
    to_update = []
    for entry in batch:
        url_normalized = normalize_url(entry["url"])
        bookmark = existing.get(url_normalized)
        is_update = bookmark is not None
        if not bookmark:
            bookmark = Bookmark(owner=user)
        _copy_bookmark_data(entry, bookmark, url_normalized)
        # Validate fields, exclude owner (no validation needed, avoids n+1)
        bookmark.clean_fields(exclude=["owner"])
        if is_update:
            to_update.append(bookmark)
        else:
            to_create.append(bookmark)

    # bulk_create/bulk_update skip Model.save(), so url_normalized was set
    # manually in _copy_bookmark_data above
    Bookmark.objects.bulk_update(to_update, BOOKMARK_UPDATE_FIELDS)
    Bookmark.objects.bulk_create(to_create)
    result.bookmarks_created += len(to_create)
    result.bookmarks_updated += len(to_update)

    # Reload to obtain primary keys (bulk_create may not return them), then
    # reset each bookmark's tag set to exactly match the backup, including
    # removals on updated bookmarks
    reloaded = {
        bookmark.url_normalized: bookmark
        for bookmark in Bookmark.objects.filter(
            owner=user, url_normalized__in=batch_urls_normalized
        )
    }
    affected_ids = [reloaded[url].id for url in batch_urls_normalized]

    BookmarkToTagRelationship = Bookmark.tags.through
    BookmarkToTagRelationship.objects.filter(bookmark_id__in=affected_ids).delete()

    relationships = []
    for entry in batch:
        bookmark = reloaded[normalize_url(entry["url"])]
        tags = tag_cache.get_all(entry.get("tag_names") or [])
        for tag in tags:
            relationships.append(BookmarkToTagRelationship(bookmark=bookmark, tag=tag))
    BookmarkToTagRelationship.objects.bulk_create(relationships, ignore_conflicts=True)


def _copy_bookmark_data(entry: dict, bookmark: Bookmark, url_normalized: str):
    bookmark.url = entry["url"]
    bookmark.url_normalized = url_normalized
    bookmark.title = entry.get("title", "")
    bookmark.description = entry.get("description", "")
    bookmark.notes = entry.get("notes", "")
    bookmark.web_archive_snapshot_url = entry.get("web_archive_snapshot_url", "")
    bookmark.unread = bool(entry.get("unread", False))
    bookmark.is_archived = bool(entry.get("is_archived", False))
    bookmark.shared = bool(entry.get("shared", False))
    bookmark.date_added = (
        _parse_datetime(entry.get("date_added"), "bookmarks.date_added")
        or timezone.now()
    )
    bookmark.date_modified = (
        _parse_datetime(entry.get("date_modified"), "bookmarks.date_modified")
        or bookmark.date_added
    )
    bookmark.date_accessed = _parse_datetime(
        entry.get("date_accessed"), "bookmarks.date_accessed"
    )


def _restore_bundles(
    bundles_data: list,
    user: User,
    result: WorkspaceImportResult,
):
    for entry in bundles_data:
        name = entry.get("name")
        if not name:
            raise WorkspaceImportError("Encountered a bundle without a name.")
        # Bundle names are not unique; match the first deterministically
        bundle = (
            BookmarkBundle.objects.filter(owner=user, name=name)
            .order_by("order", "id")
            .first()
        )
        is_update = bundle is not None
        if not bundle:
            bundle = BookmarkBundle(owner=user)

        bundle.name = name
        bundle.search = entry.get("search", "")
        bundle.any_tags = entry.get("any_tags", "")
        bundle.all_tags = entry.get("all_tags", "")
        bundle.excluded_tags = entry.get("excluded_tags", "")
        bundle.filter_unread = entry.get(
            "filter_unread", BookmarkBundle.FILTER_STATE_OFF
        )
        bundle.filter_shared = entry.get(
            "filter_shared", BookmarkBundle.FILTER_STATE_OFF
        )
        bundle.order = entry.get("order", 0)
        # date_created/date_modified are auto fields; validate and set the rest
        bundle.clean_fields(exclude=["owner", "date_created", "date_modified"])
        bundle.save()

        # save() stamps the auto fields with now(); force-preserve the backup's
        # timestamps via update(), which bypasses auto_now/auto_now_add
        update_fields = {}
        date_created = _parse_datetime(
            entry.get("date_created"), "bundles.date_created"
        )
        if date_created is not None:
            update_fields["date_created"] = date_created
        date_modified = _parse_datetime(
            entry.get("date_modified"), "bundles.date_modified"
        )
        if date_modified is not None:
            update_fields["date_modified"] = date_modified
        if update_fields:
            BookmarkBundle.objects.filter(pk=bundle.pk).update(**update_fields)

        if is_update:
            result.bundles_updated += 1
        else:
            result.bundles_created += 1
