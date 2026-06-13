import gzip
import json
from datetime import UTC, datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bookmarks.models import Bookmark, BookmarkBundle, Tag
from bookmarks.services import tasks
from bookmarks.services.workspace_backup import create_backup
from bookmarks.services.workspace_restore import (
    RestoreOptions,
    RestoreResult,
    restore_backup,
)
from bookmarks.tests.helpers import BookmarkFactoryMixin, disable_logging


class WorkspaceRestoreTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()

    def _make_backup_data(self, **overrides):
        """Create a gzipped backup with default test data."""
        default = {
            "version": 1,
            "created_at": "2025-06-13T12:00:00+00:00",
            "tags": [],
            "bookmarks": [],
            "bundles": [],
            "profile": {},
        }
        default.update(overrides)
        json_bytes = json.dumps(default).encode("utf-8")
        return gzip.compress(json_bytes)

    # --- Merge mode tests ---

    def test_merge_creates_new_bookmarks(self):
        data = self._make_backup_data(
            bookmarks=[
                {
                    "url": "https://example.com/1",
                    "title": "Bookmark 1",
                    "description": "Desc 1",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": [],
                },
                {
                    "url": "https://example.com/2",
                    "title": "Bookmark 2",
                    "description": "Desc 2",
                    "notes": "Notes 2",
                    "web_archive_snapshot_url": "",
                    "unread": True,
                    "is_archived": True,
                    "shared": True,
                    "date_added": "2025-02-01T00:00:00+00:00",
                    "date_modified": "2025-02-01T00:00:00+00:00",
                    "date_accessed": "2025-03-01T00:00:00+00:00",
                    "tag_names": [],
                },
            ]
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(result.bookmarks_created, 2)
        self.assertEqual(Bookmark.objects.count(), 2)

        bm1 = Bookmark.objects.get(url="https://example.com/1")
        self.assertEqual(bm1.title, "Bookmark 1")
        self.assertEqual(bm1.description, "Desc 1")
        self.assertFalse(bm1.unread)
        self.assertFalse(bm1.is_archived)
        self.assertFalse(bm1.shared)

        bm2 = Bookmark.objects.get(url="https://example.com/2")
        self.assertEqual(bm2.title, "Bookmark 2")
        self.assertEqual(bm2.notes, "Notes 2")
        self.assertTrue(bm2.unread)
        self.assertTrue(bm2.is_archived)
        self.assertTrue(bm2.shared)
        self.assertIsNotNone(bm2.date_accessed)

    def test_merge_updates_existing_bookmarks(self):
        self.setup_bookmark(
            url="https://example.com/1",
            title="Old Title",
            description="Old Desc",
        )

        data = self._make_backup_data(
            bookmarks=[
                {
                    "url": "https://example.com/1",
                    "title": "New Title",
                    "description": "New Desc",
                    "notes": "New Notes",
                    "web_archive_snapshot_url": "",
                    "unread": True,
                    "is_archived": True,
                    "shared": True,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-06-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": [],
                }
            ]
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(result.bookmarks_updated, 1)
        self.assertEqual(result.bookmarks_created, 0)
        self.assertEqual(Bookmark.objects.count(), 1)

        bm = Bookmark.objects.get(url="https://example.com/1")
        self.assertEqual(bm.title, "New Title")
        self.assertEqual(bm.description, "New Desc")
        self.assertEqual(bm.notes, "New Notes")
        self.assertTrue(bm.unread)
        self.assertTrue(bm.is_archived)
        self.assertTrue(bm.shared)

    def test_merge_preserves_bookmarks_not_in_backup(self):
        existing = self.setup_bookmark(
            url="https://existing.com",
            title="Existing Bookmark",
        )

        data = self._make_backup_data(
            bookmarks=[
                {
                    "url": "https://new.com",
                    "title": "New Bookmark",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": [],
                }
            ]
        )

        restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(Bookmark.objects.count(), 2)
        self.assertTrue(Bookmark.objects.filter(url="https://existing.com").exists())
        self.assertTrue(Bookmark.objects.filter(url="https://new.com").exists())

    def test_merge_creates_missing_tags(self):
        self.setup_tag(name="existing-tag")

        data = self._make_backup_data(
            tags=[
                {"name": "existing-tag", "date_added": "2025-01-01T00:00:00+00:00"},
                {"name": "new-tag", "date_added": "2025-02-01T00:00:00+00:00"},
            ]
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(result.tags_created, 1)
        self.assertEqual(Tag.objects.count(), 2)
        self.assertTrue(Tag.objects.filter(name="existing-tag").exists())
        self.assertTrue(Tag.objects.filter(name="new-tag").exists())

    def test_merge_preserves_existing_tags(self):
        original_date = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        tag = self.setup_tag(name="my-tag")
        tag.date_added = original_date
        tag.save()

        data = self._make_backup_data(
            tags=[
                {"name": "my-tag", "date_added": "2025-06-01T00:00:00+00:00"}
            ]
        )

        restore_backup(data, self.user, RestoreOptions(mode="merge"))

        # Tag should still exist with original date (not overwritten)
        self.assertEqual(Tag.objects.count(), 1)
        restored_tag = Tag.objects.get(name="my-tag")
        self.assertEqual(restored_tag.date_added, original_date)

    def test_merge_adds_tags_to_bookmarks(self):
        data = self._make_backup_data(
            tags=[
                {"name": "tag1", "date_added": "2025-01-01T00:00:00+00:00"},
                {"name": "tag2", "date_added": "2025-01-01T00:00:00+00:00"},
            ],
            bookmarks=[
                {
                    "url": "https://example.com/1",
                    "title": "BM 1",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": ["tag1", "tag2"],
                }
            ],
        )

        restore_backup(data, self.user, RestoreOptions(mode="merge"))

        bm = Bookmark.objects.get(url="https://example.com/1")
        tag_names = sorted([t.name for t in bm.tags.all()])
        self.assertEqual(tag_names, ["tag1", "tag2"])

    def test_merge_preserves_existing_tags_on_bookmark(self):
        existing_tag = self.setup_tag(name="existing-tag")
        bm = self.setup_bookmark(url="https://example.com/1", tags=[existing_tag])

        data = self._make_backup_data(
            tags=[
                {"name": "new-tag", "date_added": "2025-01-01T00:00:00+00:00"},
            ],
            bookmarks=[
                {
                    "url": "https://example.com/1",
                    "title": "BM 1",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": ["new-tag"],
                }
            ],
        )

        restore_backup(data, self.user, RestoreOptions(mode="merge"))

        bm.refresh_from_db()
        tag_names = sorted([t.name for t in bm.tags.all()])
        self.assertEqual(tag_names, ["existing-tag", "new-tag"])

    def test_merge_creates_new_bundles(self):
        data = self._make_backup_data(
            bundles=[
                {
                    "name": "Dev Reading",
                    "search": "python",
                    "any_tags": "python",
                    "all_tags": "",
                    "excluded_tags": "",
                    "filter_unread": "yes",
                    "filter_shared": "off",
                    "order": 1,
                }
            ]
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(result.bundles_created, 1)
        self.assertEqual(BookmarkBundle.objects.count(), 1)

        bundle = BookmarkBundle.objects.get(name="Dev Reading")
        self.assertEqual(bundle.search, "python")
        self.assertEqual(bundle.any_tags, "python")
        self.assertEqual(bundle.filter_unread, "yes")
        self.assertEqual(bundle.order, 1)

    def test_merge_updates_existing_bundles(self):
        self.setup_bundle(name="Dev Reading", search="old search", order=1)

        data = self._make_backup_data(
            bundles=[
                {
                    "name": "Dev Reading",
                    "search": "new search",
                    "any_tags": "python django",
                    "all_tags": "",
                    "excluded_tags": "",
                    "filter_unread": "no",
                    "filter_shared": "yes",
                    "order": 5,
                }
            ]
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(result.bundles_updated, 1)
        self.assertEqual(result.bundles_created, 0)

        bundle = BookmarkBundle.objects.get(name="Dev Reading")
        self.assertEqual(bundle.search, "new search")
        self.assertEqual(bundle.any_tags, "python django")
        self.assertEqual(bundle.filter_unread, "no")
        self.assertEqual(bundle.filter_shared, "yes")
        self.assertEqual(bundle.order, 5)

    def test_merge_restores_profile_settings(self):
        data = self._make_backup_data(
            profile={
                "theme": "dark",
                "bookmark_date_display": "absolute",
                "enable_sharing": True,
                "items_per_page": 50,
                "custom_css": "body { color: blue; }",
            }
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertTrue(result.profile_updated)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, "dark")
        self.assertEqual(self.user.profile.bookmark_date_display, "absolute")
        self.assertTrue(self.user.profile.enable_sharing)
        self.assertEqual(self.user.profile.items_per_page, 50)
        self.assertEqual(self.user.profile.custom_css, "body { color: blue; }")

    def test_merge_preserves_profile_fields_not_in_backup(self):
        self.user.profile.theme = "dark"
        self.user.profile.enable_favicons = True
        self.user.profile.save()

        data = self._make_backup_data(
            profile={
                "theme": "light",
            }
        )

        restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, "light")
        # enable_favicons should remain unchanged
        self.assertTrue(self.user.profile.enable_favicons)

    # --- Replace mode tests ---

    def test_replace_deletes_existing_bookmarks(self):
        self.setup_bookmark(url="https://old.com")

        data = self._make_backup_data(
            bookmarks=[
                {
                    "url": "https://new.com",
                    "title": "New",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": [],
                }
            ]
        )

        restore_backup(data, self.user, RestoreOptions(mode="replace"))

        self.assertEqual(Bookmark.objects.count(), 1)
        self.assertFalse(Bookmark.objects.filter(url="https://old.com").exists())
        self.assertTrue(Bookmark.objects.filter(url="https://new.com").exists())

    def test_replace_deletes_existing_tags(self):
        self.setup_tag(name="old-tag")

        data = self._make_backup_data(
            tags=[
                {"name": "new-tag", "date_added": "2025-01-01T00:00:00+00:00"}
            ]
        )

        restore_backup(data, self.user, RestoreOptions(mode="replace"))

        self.assertEqual(Tag.objects.count(), 1)
        self.assertFalse(Tag.objects.filter(name="old-tag").exists())
        self.assertTrue(Tag.objects.filter(name="new-tag").exists())

    def test_replace_deletes_existing_bundles(self):
        self.setup_bundle(name="Old Bundle")

        data = self._make_backup_data(
            bundles=[
                {
                    "name": "New Bundle",
                    "search": "",
                    "any_tags": "",
                    "all_tags": "",
                    "excluded_tags": "",
                    "filter_unread": "off",
                    "filter_shared": "off",
                    "order": 0,
                }
            ]
        )

        restore_backup(data, self.user, RestoreOptions(mode="replace"))

        self.assertEqual(BookmarkBundle.objects.count(), 1)
        self.assertFalse(BookmarkBundle.objects.filter(name="Old Bundle").exists())
        self.assertTrue(BookmarkBundle.objects.filter(name="New Bundle").exists())

    def test_replace_creates_all_from_backup(self):
        data = self._make_backup_data(
            tags=[
                {"name": "tag1", "date_added": "2025-01-01T00:00:00+00:00"},
                {"name": "tag2", "date_added": "2025-01-01T00:00:00+00:00"},
            ],
            bookmarks=[
                {
                    "url": "https://example.com/1",
                    "title": "BM 1",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": True,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": ["tag1"],
                },
                {
                    "url": "https://example.com/2",
                    "title": "BM 2",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": True,
                    "shared": True,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": ["tag2"],
                },
            ],
            bundles=[
                {
                    "name": "Bundle 1",
                    "search": "",
                    "any_tags": "",
                    "all_tags": "",
                    "excluded_tags": "",
                    "filter_unread": "off",
                    "filter_shared": "off",
                    "order": 0,
                }
            ],
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="replace"))

        self.assertEqual(result.bookmarks_created, 2)
        self.assertEqual(result.tags_created, 2)
        self.assertEqual(result.bundles_created, 1)

    def test_replace_restores_profile(self):
        data = self._make_backup_data(
            profile={
                "theme": "dark",
                "items_per_page": 100,
            }
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="replace"))

        self.assertTrue(result.profile_updated)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, "dark")
        self.assertEqual(self.user.profile.items_per_page, 100)

    # --- Round-trip tests ---

    def test_full_roundtrip(self):
        """Complete backup → clear → restore → verify all data matches."""
        # Setup original data
        tag1 = self.setup_tag(name="python")
        tag2 = self.setup_tag(name="django")
        bm1 = self.setup_bookmark(
            url="https://python.org",
            title="Python",
            description="Python programming language",
            notes="Great language",
            unread=True,
            shared=True,
            tags=[tag1],
            added=datetime(2025, 1, 1, tzinfo=UTC),
            modified=datetime(2025, 3, 1, tzinfo=UTC),
        )
        bm2 = self.setup_bookmark(
            url="https://djangoproject.com",
            title="Django",
            description="Web framework",
            is_archived=True,
            tags=[tag1, tag2],
            added=datetime(2025, 2, 1, tzinfo=UTC),
            modified=datetime(2025, 4, 1, tzinfo=UTC),
        )
        self.setup_bundle(
            name="Web Dev",
            search="web",
            any_tags="python django",
            filter_unread="yes",
            order=1,
        )
        self.user.profile.theme = "dark"
        self.user.profile.items_per_page = 50
        self.user.profile.save()

        # Create backup
        backup_data = create_backup(self.user)

        # Clear all data
        Bookmark.objects.filter(owner=self.user).delete()
        BookmarkBundle.objects.filter(owner=self.user).delete()
        Tag.objects.filter(owner=self.user).delete()

        self.assertEqual(Bookmark.objects.count(), 0)
        self.assertEqual(Tag.objects.count(), 0)
        self.assertEqual(BookmarkBundle.objects.count(), 0)

        # Reset profile
        self.user.profile.theme = "auto"
        self.user.profile.items_per_page = 30
        self.user.profile.save()

        # Restore from backup
        result = restore_backup(backup_data, self.user, RestoreOptions(mode="merge"))

        # Verify counts
        self.assertEqual(result.bookmarks_created, 2)
        self.assertEqual(result.tags_created, 2)
        self.assertEqual(result.bundles_created, 1)
        self.assertTrue(result.profile_updated)

        # Verify bookmarks
        self.assertEqual(Bookmark.objects.count(), 2)
        restored_bm1 = Bookmark.objects.get(url="https://python.org")
        self.assertEqual(restored_bm1.title, "Python")
        self.assertEqual(restored_bm1.description, "Python programming language")
        self.assertEqual(restored_bm1.notes, "Great language")
        self.assertTrue(restored_bm1.unread)
        self.assertTrue(restored_bm1.shared)
        self.assertFalse(restored_bm1.is_archived)

        restored_bm2 = Bookmark.objects.get(url="https://djangoproject.com")
        self.assertEqual(restored_bm2.title, "Django")
        self.assertTrue(restored_bm2.is_archived)

        # Verify tags on bookmarks
        bm1_tags = sorted([t.name for t in restored_bm1.tags.all()])
        self.assertEqual(bm1_tags, ["python"])
        bm2_tags = sorted([t.name for t in restored_bm2.tags.all()])
        self.assertEqual(bm2_tags, ["django", "python"])

        # Verify tags
        self.assertEqual(Tag.objects.count(), 2)
        self.assertTrue(Tag.objects.filter(name="python").exists())
        self.assertTrue(Tag.objects.filter(name="django").exists())

        # Verify bundles
        self.assertEqual(BookmarkBundle.objects.count(), 1)
        bundle = BookmarkBundle.objects.get(name="Web Dev")
        self.assertEqual(bundle.search, "web")
        self.assertEqual(bundle.any_tags, "python django")
        self.assertEqual(bundle.filter_unread, "yes")
        self.assertEqual(bundle.order, 1)

        # Verify profile
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, "dark")
        self.assertEqual(self.user.profile.items_per_page, 50)

    def test_roundtrip_preserves_bookmark_states(self):
        """Verify unread, shared, is_archived, notes survive round-trip."""
        self.setup_bookmark(
            url="https://unread.com",
            unread=True,
            notes="Unread notes",
        )
        self.setup_bookmark(
            url="https://shared.com",
            shared=True,
            notes="Shared notes",
        )
        self.setup_bookmark(
            url="https://archived.com",
            is_archived=True,
            notes="Archived notes",
        )

        backup_data = create_backup(self.user)
        Bookmark.objects.filter(owner=self.user).delete()
        restore_backup(backup_data, self.user, RestoreOptions(mode="merge"))

        bm_unread = Bookmark.objects.get(url="https://unread.com")
        self.assertTrue(bm_unread.unread)
        self.assertEqual(bm_unread.notes, "Unread notes")

        bm_shared = Bookmark.objects.get(url="https://shared.com")
        self.assertTrue(bm_shared.shared)
        self.assertEqual(bm_shared.notes, "Shared notes")

        bm_archived = Bookmark.objects.get(url="https://archived.com")
        self.assertTrue(bm_archived.is_archived)
        self.assertEqual(bm_archived.notes, "Archived notes")

    def test_roundtrip_preserves_bundles(self):
        """Verify all bundle fields survive round-trip."""
        self.setup_bundle(
            name="Full Bundle",
            search="test query",
            any_tags="tag1 tag2",
            all_tags="tag3",
            excluded_tags="tag4",
            filter_unread="yes",
            filter_shared="no",
            order=42,
        )

        backup_data = create_backup(self.user)
        BookmarkBundle.objects.filter(owner=self.user).delete()
        restore_backup(backup_data, self.user, RestoreOptions(mode="merge"))

        bundle = BookmarkBundle.objects.get(name="Full Bundle")
        self.assertEqual(bundle.search, "test query")
        self.assertEqual(bundle.any_tags, "tag1 tag2")
        self.assertEqual(bundle.all_tags, "tag3")
        self.assertEqual(bundle.excluded_tags, "tag4")
        self.assertEqual(bundle.filter_unread, "yes")
        self.assertEqual(bundle.filter_shared, "no")
        self.assertEqual(bundle.order, 42)

    def test_roundtrip_preserves_profile(self):
        """Verify all profile preference fields survive round-trip."""
        profile = self.user.profile
        profile.theme = "dark"
        profile.bookmark_date_display = "absolute"
        profile.bookmark_description_display = "separate"
        profile.bookmark_description_max_lines = 3
        profile.bookmark_link_target = "_self"
        profile.web_archive_integration = "enabled"
        profile.tag_search = "lax"
        profile.tag_grouping = "disabled"
        profile.enable_sharing = True
        profile.enable_public_sharing = True
        profile.enable_favicons = True
        profile.enable_preview_images = True
        profile.display_url = True
        profile.permanent_notes = True
        profile.custom_css = "body { color: red; }"
        profile.auto_tagging_rules = "youtube.com video"
        profile.items_per_page = 50
        profile.sticky_pagination = True
        profile.collapse_side_panel = True
        profile.hide_bundles = True
        profile.search_preferences = {"sort": "title_asc"}
        profile.save()

        backup_data = create_backup(self.user)

        # Reset profile to defaults
        profile.theme = "auto"
        profile.items_per_page = 30
        profile.enable_sharing = False
        profile.save()

        restore_backup(backup_data, self.user, RestoreOptions(mode="merge"))

        profile.refresh_from_db()
        self.assertEqual(profile.theme, "dark")
        self.assertEqual(profile.bookmark_date_display, "absolute")
        self.assertEqual(profile.bookmark_description_display, "separate")
        self.assertEqual(profile.bookmark_description_max_lines, 3)
        self.assertEqual(profile.bookmark_link_target, "_self")
        self.assertEqual(profile.web_archive_integration, "enabled")
        self.assertEqual(profile.tag_search, "lax")
        self.assertEqual(profile.tag_grouping, "disabled")
        self.assertTrue(profile.enable_sharing)
        self.assertTrue(profile.enable_public_sharing)
        self.assertTrue(profile.enable_favicons)
        self.assertTrue(profile.enable_preview_images)
        self.assertTrue(profile.display_url)
        self.assertTrue(profile.permanent_notes)
        self.assertEqual(profile.custom_css, "body { color: red; }")
        self.assertEqual(profile.auto_tagging_rules, "youtube.com video")
        self.assertEqual(profile.items_per_page, 50)
        self.assertTrue(profile.sticky_pagination)
        self.assertTrue(profile.collapse_side_panel)
        self.assertTrue(profile.hide_bundles)
        self.assertEqual(profile.search_preferences, {"sort": "title_asc"})

    def test_roundtrip_preserves_tag_dates(self):
        """Verify tag date_added survives round-trip."""
        original_date = datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC)
        tag = self.setup_tag(name="old-tag")
        tag.date_added = original_date
        tag.save()

        backup_data = create_backup(self.user)
        Tag.objects.filter(owner=self.user).delete()
        restore_backup(backup_data, self.user, RestoreOptions(mode="merge"))

        restored_tag = Tag.objects.get(name="old-tag")
        self.assertEqual(restored_tag.date_added, original_date)

    # --- Error handling tests ---

    def test_invalid_gzip_raises_error(self):
        with self.assertRaises(ValueError) as ctx:
            restore_backup(b"not gzip data", self.user)
        self.assertIn("gzip", str(ctx.exception).lower())

    def test_invalid_json_raises_error(self):
        data = gzip.compress(b"not json")
        with self.assertRaises(ValueError) as ctx:
            restore_backup(data, self.user)
        self.assertIn("json", str(ctx.exception).lower())

    def test_missing_version_raises_error(self):
        data = gzip.compress(json.dumps({"bookmarks": []}).encode())
        with self.assertRaises(ValueError) as ctx:
            restore_backup(data, self.user)
        self.assertIn("version", str(ctx.exception).lower())

    def test_unsupported_version_raises_error(self):
        data = gzip.compress(json.dumps({"version": 99}).encode())
        with self.assertRaises(ValueError) as ctx:
            restore_backup(data, self.user)
        self.assertIn("99", str(ctx.exception))

    @disable_logging
    def test_skips_invalid_bookmarks(self):
        data = self._make_backup_data(
            bookmarks=[
                {
                    "url": "https://valid.com",
                    "title": "Valid",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": [],
                },
                {
                    "url": "",  # Invalid: empty URL
                    "title": "Invalid",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": [],
                },
            ]
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(result.bookmarks_created, 1)
        self.assertEqual(result.bookmarks_failed, 1)
        self.assertEqual(Bookmark.objects.count(), 1)

    def test_skips_long_tag_names(self):
        long_tag = "a" * 65
        data = self._make_backup_data(
            tags=[
                {"name": long_tag, "date_added": "2025-01-01T00:00:00+00:00"},
                {"name": "valid-tag", "date_added": "2025-01-01T00:00:00+00:00"},
            ]
        )

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(result.tags_created, 1)
        self.assertEqual(Tag.objects.count(), 1)
        self.assertEqual(Tag.objects.first().name, "valid-tag")

    def test_restore_empty_backup(self):
        data = self._make_backup_data()

        result = restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.assertEqual(result.bookmarks_created, 0)
        self.assertEqual(result.tags_created, 0)
        self.assertEqual(result.bundles_created, 0)

    def test_date_accessed_nullable(self):
        data = self._make_backup_data(
            bookmarks=[
                {
                    "url": "https://example.com",
                    "title": "Test",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": [],
                }
            ]
        )

        restore_backup(data, self.user, RestoreOptions(mode="merge"))

        bm = Bookmark.objects.get(url="https://example.com")
        self.assertIsNone(bm.date_accessed)

    def test_schedule_favicon_loading(self):
        data = self._make_backup_data()

        with patch.object(
            tasks, "schedule_bookmarks_without_favicons"
        ) as mock_schedule:
            restore_backup(data, self.user)
            mock_schedule.assert_called_once_with(self.user)

    def test_schedule_preview_loading(self):
        data = self._make_backup_data()

        with patch.object(
            tasks, "schedule_bookmarks_without_previews"
        ) as mock_schedule:
            restore_backup(data, self.user)
            mock_schedule.assert_called_once_with(self.user)

    def test_default_options_is_merge(self):
        options = RestoreOptions()
        self.assertEqual(options.mode, "merge")

    def test_profile_ignores_unknown_fields(self):
        data = self._make_backup_data(
            profile={
                "theme": "dark",
                "malicious_field": "evil_value",
                "another_unknown": 42,
            }
        )

        restore_backup(data, self.user, RestoreOptions(mode="merge"))

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, "dark")
        self.assertFalse(hasattr(self.user.profile, "malicious_field"))

    def test_does_not_affect_other_users_data(self):
        other_user = self.setup_user()
        other_bm = self.setup_bookmark(url="https://other.com", user=other_user)
        other_tag = self.setup_tag(name="other-tag", user=other_user)
        other_bundle = self.setup_bundle(name="Other Bundle", user=other_user)

        data = self._make_backup_data(
            tags=[
                {"name": "my-tag", "date_added": "2025-01-01T00:00:00+00:00"},
            ],
            bookmarks=[
                {
                    "url": "https://mine.com",
                    "title": "Mine",
                    "description": "",
                    "notes": "",
                    "web_archive_snapshot_url": "",
                    "unread": False,
                    "is_archived": False,
                    "shared": False,
                    "date_added": "2025-01-01T00:00:00+00:00",
                    "date_modified": "2025-01-01T00:00:00+00:00",
                    "date_accessed": None,
                    "tag_names": [],
                }
            ],
        )

        # Replace mode - should only delete current user's data
        restore_backup(data, self.user, RestoreOptions(mode="replace"))

        # Other user's data should be untouched
        self.assertTrue(Bookmark.objects.filter(url="https://other.com").exists())
        self.assertTrue(Tag.objects.filter(name="other-tag").exists())
        self.assertTrue(BookmarkBundle.objects.filter(name="Other Bundle").exists())

        # Current user should have only the restored data
        self.assertEqual(Bookmark.objects.filter(owner=self.user).count(), 1)
        self.assertTrue(
            Bookmark.objects.filter(owner=self.user, url="https://mine.com").exists()
        )
