import logging
from dataclasses import dataclass
from typing import Literal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from bookmarks.models import Bookmark, Tag
from bookmarks.services import tasks
from bookmarks.services.parser import NetscapeBookmark, parse
from bookmarks.utils import normalize_url, parse_timestamp
from bookmarks.validators import BookmarkURLValidator

logger = logging.getLogger(__name__)

DuplicateHandling = Literal["update", "skip"]


@dataclass
class ImportResult:
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass
class ImportOptions:
    map_private_flag: bool = False
    duplicate_handling: DuplicateHandling = "update"


@dataclass
class ImportPrecheckResult:
    total: int = 0
    new_count: int = 0
    update_count: int = 0
    skip_count: int = 0


class TagCache:
    def __init__(self, user: User):
        self.user = user
        self.cache = dict()
        # Init cache with all existing tags for that user
        tags = Tag.objects.filter(owner=user)
        for tag in tags:
            self.put(tag)

    def get(self, tag_name: str):
        tag_name_lowercase = tag_name.lower()
        if tag_name_lowercase in self.cache:
            return self.cache[tag_name_lowercase]
        else:
            return None

    def get_all(self, tag_names: list[str]):
        result = []
        for tag_name in tag_names:
            tag = self.get(tag_name)
            # Tag may not have been created if tag name exceeded maximum length
            # Prevent returning duplicates
            if tag and tag not in result:
                result.append(tag)

        return result

    def put(self, tag: Tag):
        self.cache[tag.name.lower()] = tag


def import_netscape_html(
    html: str, user: User, options: ImportOptions | None = None
) -> ImportResult:
    if options is None:
        options = ImportOptions()
    result = ImportResult()
    import_start = timezone.now()

    try:
        netscape_bookmarks = parse(html)
    except Exception:
        logging.exception("Could not read bookmarks file.")
        raise

    parse_end = timezone.now()
    logger.debug(f"Parse duration: {parse_end - import_start}")

    # Create and cache all tags beforehand
    _create_missing_tags(netscape_bookmarks, user)
    tag_cache = TagCache(user)

    # Split bookmarks to import into batches, to keep memory usage for bulk operations manageable
    batches = _get_batches(netscape_bookmarks, 200)
    for batch in batches:
        _import_batch(batch, user, options, tag_cache, result)

    # Load favicons for newly imported bookmarks
    tasks.schedule_bookmarks_without_favicons(user)
    # Load previews for newly imported bookmarks
    tasks.schedule_bookmarks_without_previews(user)

    end = timezone.now()
    logger.debug(f"Import duration: {end - import_start}")

    return result


def precheck_import(html: str, user: User) -> ImportPrecheckResult:
    """Parse and analyze an import file without writing to the database.

    Returns a summary of what would happen on import: how many bookmarks are
    new, how many would update existing ones, and how many are invalid.
    """
    result = ImportPrecheckResult()

    netscape_bookmarks = parse(html)
    result.total = len(netscape_bookmarks)

    if not netscape_bookmarks:
        return result

    url_validator = BookmarkURLValidator()

    # Separate valid from invalid bookmarks
    valid_bookmarks: list[NetscapeBookmark] = []
    for bm in netscape_bookmarks:
        if not bm.href:
            result.skip_count += 1
            continue
        try:
            url_validator(bm.href)
            valid_bookmarks.append(bm)
        except ValidationError:
            result.skip_count += 1

    if not valid_bookmarks:
        return result

    # Check which URLs already exist for this user
    valid_urls = [bm.href for bm in valid_bookmarks]
    existing_urls = set(
        Bookmark.objects.filter(owner=user, url__in=valid_urls).values_list(
            "url", flat=True
        )
    )

    for bm in valid_bookmarks:
        if bm.href in existing_urls:
            result.update_count += 1
        else:
            result.new_count += 1

    return result


def _create_missing_tags(netscape_bookmarks: list[NetscapeBookmark], user: User):
    tag_cache = TagCache(user)
    tags_to_create = []

    for netscape_bookmark in netscape_bookmarks:
        for tag_name in netscape_bookmark.tag_names:
            # Skip tag names that exceed the maximum allowed length
            if len(tag_name) > 64:
                logger.warning(
                    f"Ignoring tag '{tag_name}' (length {len(tag_name)}) as it exceeds maximum length of 64 characters"
                )
                continue

            tag = tag_cache.get(tag_name)
            if not tag:
                tag = Tag(name=tag_name, owner=user)
                tag.date_added = timezone.now()
                tags_to_create.append(tag)
                tag_cache.put(tag)

    Tag.objects.bulk_create(tags_to_create)


def _get_batches(items: list, batch_size: int):
    batches = []
    offset = 0
    num_items = len(items)

    while offset < num_items:
        batch = items[offset : min(offset + batch_size, num_items)]
        if len(batch) > 0:
            batches.append(batch)
        offset = offset + batch_size

    return batches


def _import_batch(
    netscape_bookmarks: list[NetscapeBookmark],
    user: User,
    options: ImportOptions,
    tag_cache: TagCache,
    result: ImportResult,
):
    # Query existing bookmarks
    batch_urls = [bookmark.href for bookmark in netscape_bookmarks]
    existing_bookmarks = Bookmark.objects.filter(owner=user, url__in=batch_urls)

    # Create or update bookmarks from parsed Netscape bookmarks
    bookmarks_to_create = []
    bookmarks_to_update = []
    skipped_urls = set()

    for netscape_bookmark in netscape_bookmarks:
        result.total = result.total + 1
        try:
            # Lookup existing bookmark by URL, or create new bookmark if there is no bookmark for that URL yet
            bookmark = next(
                (
                    bookmark
                    for bookmark in existing_bookmarks
                    if bookmark.url == netscape_bookmark.href
                ),
                None,
            )
            if not bookmark:
                bookmark = Bookmark(owner=user)
                is_update = False
            else:
                is_update = True

            # Skip existing bookmarks when duplicate_handling is "skip"
            if is_update and options.duplicate_handling == "skip":
                result.skipped = result.skipped + 1
                skipped_urls.add(netscape_bookmark.href)
                continue

            # Copy data from parsed bookmark
            _copy_bookmark_data(netscape_bookmark, bookmark, options)
            # Validate bookmark fields, exclude owner to prevent n+1 database query,
            # also there is no specific validation on owner
            bookmark.clean_fields(exclude=["owner"])
            # Schedule for update or insert
            if is_update:
                bookmarks_to_update.append(bookmark)
            else:
                bookmarks_to_create.append(bookmark)

            result.success = result.success + 1
        except Exception:
            shortened_bookmark_tag_str = str(netscape_bookmark)[:100] + "..."
            logging.exception("Error importing bookmark: " + shortened_bookmark_tag_str)
            result.failed = result.failed + 1

    # Bulk update bookmarks in DB
    Bookmark.objects.bulk_update(
        bookmarks_to_update,
        [
            "url",
            "url_normalized",
            "date_added",
            "date_modified",
            "unread",
            "shared",
            "title",
            "description",
            "notes",
            "owner",
        ],
    )
    # Bulk insert new bookmarks into DB
    Bookmark.objects.bulk_create(bookmarks_to_create)

    # Bulk assign tags
    # In Django 3, bulk_create does not return the auto-generated IDs when bulk inserting,
    # so we have to reload the inserted bookmarks, and match them to the parsed bookmarks by URL
    existing_bookmarks = Bookmark.objects.filter(owner=user, url__in=batch_urls)

    BookmarkToTagRelationShip = Bookmark.tags.through
    relationships = []

    for netscape_bookmark in netscape_bookmarks:
        # Skip bookmarks that were intentionally skipped (duplicate_handling="skip")
        if netscape_bookmark.href in skipped_urls:
            continue

        # Lookup bookmark by URL again
        bookmark = next(
            (
                bookmark
                for bookmark in existing_bookmarks
                if bookmark.url == netscape_bookmark.href
            ),
            None,
        )

        if not bookmark:
            # Something is wrong, we should have just created this bookmark
            shortened_bookmark_tag_str = str(netscape_bookmark)[:100] + "..."
            logging.warning(
                f"Failed to assign tags to the bookmark: {shortened_bookmark_tag_str}. Could not find bookmark by URL."
            )
            continue

        # Get tag models by string, schedule inserts for bookmark -> tag associations
        tags = tag_cache.get_all(netscape_bookmark.tag_names)
        for tag in tags:
            relationships.append(BookmarkToTagRelationShip(bookmark=bookmark, tag=tag))

    # Insert all bookmark -> tag associations at once, should ignore errors if association already exists
    BookmarkToTagRelationShip.objects.bulk_create(relationships, ignore_conflicts=True)


def _copy_bookmark_data(
    netscape_bookmark: NetscapeBookmark, bookmark: Bookmark, options: ImportOptions
):
    bookmark.url = netscape_bookmark.href
    bookmark.url_normalized = normalize_url(bookmark.url)
    if netscape_bookmark.date_added:
        bookmark.date_added = parse_timestamp(netscape_bookmark.date_added)
    else:
        bookmark.date_added = timezone.now()
    if netscape_bookmark.date_modified:
        bookmark.date_modified = parse_timestamp(netscape_bookmark.date_modified)
    else:
        bookmark.date_modified = bookmark.date_added
    bookmark.unread = netscape_bookmark.to_read
    if netscape_bookmark.title:
        bookmark.title = netscape_bookmark.title
    if netscape_bookmark.description:
        bookmark.description = netscape_bookmark.description
    if netscape_bookmark.notes:
        bookmark.notes = netscape_bookmark.notes
    if options.map_private_flag and not netscape_bookmark.private:
        bookmark.shared = True
    if netscape_bookmark.archived:
        bookmark.is_archived = True
