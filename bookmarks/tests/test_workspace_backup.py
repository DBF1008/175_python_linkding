import gzip
import json
from datetime import UTC, datetime

from django.test import TestCase
from django.utils import timezone

from bookmarks.models import Bookmark, BookmarkBundle, Tag
from bookmarks.services.workspace_backup import create_backup
from bookmarks.tests.helpers import BookmarkFactoryMixin


class WorkspaceBackupTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()

    def _parse_backup(self, data: bytes) -> dict:
        return json.loads(gzip.decompress(data))

    def test_create_backup_returns_gzipped_json(self):
        data = create_backup(self.user)
        # Should be valid gzip
        json_bytes = gzip.decompress(data)
        # Should be valid JSON
        parsed = json.loads(json_bytes)
        self.assertIsInstance(parsed, dict)

    def test_backup_contains_version_and_timestamp(self):
        data = create_backup(self.user)
        parsed = self._parse_backup(data)
        self.assertEqual(parsed["version"], 1)
        self.assertIn("created_at", parsed)
        # Should be a valid ISO timestamp
        datetime.fromisoformat(parsed["created_at"])

    def test_backup_contains_tags(self):
        tag1 = self.setup_tag(name="python")
        tag2 = self.setup_tag(name="django")

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        tags = parsed["tags"]
        self.assertEqual(len(tags), 2)

        tag_names = [t["name"] for t in tags]
        self.assertIn("python", tag_names)
        self.assertIn("django", tag_names)

        # Check date_added is present and valid
        for tag in tags:
            self.assertIn("date_added", tag)
            datetime.fromisoformat(tag["date_added"])

    def test_backup_contains_bookmarks(self):
        tag = self.setup_tag(name="python")
        bookmark = self.setup_bookmark(
            url="https://example.com",
            title="Example",
            description="A test bookmark",
            notes="My personal notes",
            unread=True,
            is_archived=True,
            shared=True,
            web_archive_snapshot_url="https://web.archive.org/web/123",
            tags=[tag],
            added=datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC),
            modified=datetime(2025, 6, 1, 8, 0, 0, tzinfo=UTC),
        )

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        bookmarks = parsed["bookmarks"]
        self.assertEqual(len(bookmarks), 1)

        bm = bookmarks[0]
        self.assertEqual(bm["url"], "https://example.com")
        self.assertEqual(bm["title"], "Example")
        self.assertEqual(bm["description"], "A test bookmark")
        self.assertEqual(bm["notes"], "My personal notes")
        self.assertTrue(bm["unread"])
        self.assertTrue(bm["is_archived"])
        self.assertTrue(bm["shared"])
        self.assertEqual(bm["web_archive_snapshot_url"], "https://web.archive.org/web/123")
        self.assertEqual(bm["tag_names"], ["python"])
        self.assertEqual(bm["date_added"], "2025-01-15T10:30:00+00:00")
        self.assertEqual(bm["date_modified"], "2025-06-01T08:00:00+00:00")

    def test_backup_contains_bundles(self):
        bundle = self.setup_bundle(
            name="Dev Reading",
            search="python",
            any_tags="python django",
            all_tags="tutorial",
            excluded_tags="advanced",
            filter_unread="yes",
            filter_shared="no",
            order=5,
        )

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        bundles = parsed["bundles"]
        self.assertEqual(len(bundles), 1)

        b = bundles[0]
        self.assertEqual(b["name"], "Dev Reading")
        self.assertEqual(b["search"], "python")
        self.assertEqual(b["any_tags"], "python django")
        self.assertEqual(b["all_tags"], "tutorial")
        self.assertEqual(b["excluded_tags"], "advanced")
        self.assertEqual(b["filter_unread"], "yes")
        self.assertEqual(b["filter_shared"], "no")
        self.assertEqual(b["order"], 5)

    def test_backup_contains_profile(self):
        profile = self.user.profile
        profile.theme = "dark"
        profile.bookmark_date_display = "absolute"
        profile.enable_sharing = True
        profile.enable_favicons = True
        profile.items_per_page = 50
        profile.custom_css = "body { color: red; }"
        profile.auto_tagging_rules = "youtube.com video"
        profile.search_preferences = {"sort": "title_asc"}
        profile.save()

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        profile_data = parsed["profile"]
        self.assertEqual(profile_data["theme"], "dark")
        self.assertEqual(profile_data["bookmark_date_display"], "absolute")
        self.assertTrue(profile_data["enable_sharing"])
        self.assertTrue(profile_data["enable_favicons"])
        self.assertEqual(profile_data["items_per_page"], 50)
        self.assertEqual(profile_data["custom_css"], "body { color: red; }")
        self.assertEqual(profile_data["auto_tagging_rules"], "youtube.com video")
        self.assertEqual(profile_data["search_preferences"], {"sort": "title_asc"})

    def test_backup_excludes_other_user_data(self):
        other_user = self.setup_user()
        self.setup_bookmark(url="https://mine.com")
        self.setup_bookmark(url="https://theirs.com", user=other_user)
        self.setup_tag(name="my-tag")
        self.setup_tag(name="their-tag", user=other_user)
        self.setup_bundle(name="My Bundle")
        self.setup_bundle(name="Their Bundle", user=other_user)

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        bookmark_urls = [bm["url"] for bm in parsed["bookmarks"]]
        self.assertIn("https://mine.com", bookmark_urls)
        self.assertNotIn("https://theirs.com", bookmark_urls)

        tag_names = [t["name"] for t in parsed["tags"]]
        self.assertIn("my-tag", tag_names)
        self.assertNotIn("their-tag", tag_names)

        bundle_names = [b["name"] for b in parsed["bundles"]]
        self.assertIn("My Bundle", bundle_names)
        self.assertNotIn("Their Bundle", bundle_names)

    def test_backup_excludes_computed_fields(self):
        self.setup_bookmark(
            url="https://example.com",
            favicon_file="favicon.png",
            preview_image_file="preview.png",
        )

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        bm = parsed["bookmarks"][0]
        self.assertNotIn("url_normalized", bm)
        self.assertNotIn("favicon_file", bm)
        self.assertNotIn("preview_image_file", bm)
        self.assertNotIn("latest_snapshot", bm)

        profile_data = parsed["profile"]
        self.assertNotIn("custom_css_hash", profile_data)

    def test_backup_excludes_assets(self):
        bookmark = self.setup_bookmark()
        self.setup_asset(bookmark=bookmark)

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        self.assertNotIn("assets", parsed)

    def test_backup_handles_empty_workspace(self):
        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        self.assertEqual(parsed["tags"], [])
        self.assertEqual(parsed["bookmarks"], [])
        self.assertEqual(parsed["bundles"], [])
        self.assertIn("profile", parsed)

    def test_backup_date_accessed_nullable(self):
        self.setup_bookmark(url="https://example.com")

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        bm = parsed["bookmarks"][0]
        self.assertIsNone(bm["date_accessed"])

    def test_backup_date_accessed_present(self):
        bookmark = self.setup_bookmark(url="https://example.com")
        bookmark.date_accessed = datetime(2025, 3, 15, 14, 0, 0, tzinfo=UTC)
        bookmark.save()

        data = create_backup(self.user)
        parsed = self._parse_backup(data)

        bm = parsed["bookmarks"][0]
        self.assertEqual(bm["date_accessed"], "2025-03-15T14:00:00+00:00")
