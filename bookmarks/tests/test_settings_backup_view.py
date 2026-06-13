import gzip
import io
import json
from datetime import UTC, datetime
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Bookmark, BookmarkBundle, Tag
from bookmarks.services.workspace_backup import create_backup
from bookmarks.tests.helpers import BookmarkFactoryMixin, disable_logging


class SettingsBackupViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def test_backup_download_returns_file(self):
        self.setup_bookmark()

        response = self.client.get(reverse("linkding:settings.backup"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["content-type"], "application/gzip")

        # Verify it's valid gzipped JSON
        data = json.loads(gzip.decompress(response.content))
        self.assertEqual(data["version"], 1)
        self.assertIn("bookmarks", data)

    def test_backup_download_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("linkding:settings.backup"), follow=True)

        self.assertRedirects(
            response,
            reverse("login") + "?next=" + reverse("linkding:settings.backup"),
        )

    def test_backup_download_only_exports_user_data(self):
        other_user = self.setup_user()
        self.setup_bookmark(url="https://mine.com")
        self.setup_bookmark(url="https://theirs.com", user=other_user)

        response = self.client.get(reverse("linkding:settings.backup"), follow=True)

        data = json.loads(gzip.decompress(response.content))
        urls = [bm["url"] for bm in data["bookmarks"]]
        self.assertIn("https://mine.com", urls)
        self.assertNotIn("https://theirs.com", urls)

    def test_backup_download_error_handling(self):
        with patch(
            "bookmarks.services.workspace_backup.create_backup"
        ) as mock_backup:
            mock_backup.side_effect = Exception("Backup failed")
            response = self.client.get(reverse("linkding:settings.backup"), follow=True)

            self.assertTemplateUsed(response, "settings/general.html")
            self.assertContains(response, '<div class="has-error">')
            self.assertContains(
                response, "An error occurred during workspace backup."
            )

    def test_backup_filename_includes_date_and_time(self):
        fixed_time = datetime(2023, 5, 15, 14, 30, 45, tzinfo=UTC)

        with patch("bookmarks.views.settings.timezone.now", return_value=fixed_time):
            response = self.client.get(reverse("linkding:settings.backup"), follow=True)

        expected = 'attachment; filename="linkding_backup_2023-05-15_14-30-45.json.gz"'
        self.assertEqual(response["Content-Disposition"], expected)


class SettingsRestoreViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def _create_backup_file(self, **overrides):
        """Create an in-memory backup file for upload."""
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
        gzipped = gzip.compress(json_bytes)
        return SimpleUploadedFile(
            "backup.json.gz", gzipped, content_type="application/gzip"
        )

    def test_restore_upload_success(self):
        backup_file = self._create_backup_file(
            bookmarks=[
                {
                    "url": "https://example.com",
                    "title": "Example",
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

        response = self.client.post(
            reverse("linkding:settings.restore"),
            {"backup_file": backup_file, "restore_mode": "merge"},
            follow=True,
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertEqual(Bookmark.objects.count(), 1)
        self.assertContains(response, "toast-success")

    def test_restore_upload_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("linkding:settings.restore"), follow=True)

        self.assertRedirects(
            response,
            reverse("login") + "?next=" + reverse("linkding:settings.restore"),
        )

    def test_restore_upload_no_file(self):
        response = self.client.post(
            reverse("linkding:settings.restore"),
            {"restore_mode": "merge"},
            follow=True,
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertContains(response, "Please select a backup file to restore.")

    @disable_logging
    def test_restore_upload_invalid_file(self):
        invalid_file = SimpleUploadedFile(
            "backup.json.gz", b"not valid gzip", content_type="application/gzip"
        )

        response = self.client.post(
            reverse("linkding:settings.restore"),
            {"backup_file": invalid_file, "restore_mode": "merge"},
            follow=True,
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertContains(response, "toast-error")

    def test_restore_merge_mode(self):
        self.setup_bookmark(url="https://existing.com")

        backup_file = self._create_backup_file(
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

        self.client.post(
            reverse("linkding:settings.restore"),
            {"backup_file": backup_file, "restore_mode": "merge"},
            follow=True,
        )

        # Both bookmarks should exist in merge mode
        self.assertEqual(Bookmark.objects.count(), 2)

    def test_restore_replace_mode(self):
        self.setup_bookmark(url="https://existing.com")

        backup_file = self._create_backup_file(
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

        self.client.post(
            reverse("linkding:settings.restore"),
            {"backup_file": backup_file, "restore_mode": "replace"},
            follow=True,
        )

        # Only restored bookmark should exist in replace mode
        self.assertEqual(Bookmark.objects.count(), 1)
        self.assertTrue(Bookmark.objects.filter(url="https://new.com").exists())
        self.assertFalse(Bookmark.objects.filter(url="https://existing.com").exists())

    def test_restore_default_mode_is_merge(self):
        self.setup_bookmark(url="https://existing.com")

        backup_file = self._create_backup_file(
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

        self.client.post(
            reverse("linkding:settings.restore"),
            {"backup_file": backup_file},
            follow=True,
        )

        # Both bookmarks should exist (merge is default)
        self.assertEqual(Bookmark.objects.count(), 2)

    def test_netscape_export_still_works(self):
        self.setup_bookmark()

        response = self.client.get(reverse("linkding:settings.export"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["content-type"], "text/plain; charset=UTF-8")

    def test_netscape_import_still_works(self):
        html_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="https://example.com" ADD_DATE="1" TOREAD="0" PRIVATE="1" TAGS="">Example</A>
<DD>Description
</DL><p>"""
        import_file = SimpleUploadedFile(
            "bookmarks.html", html_content.encode("utf-8"), content_type="text/html"
        )

        response = self.client.post(
            reverse("linkding:settings.import"),
            {"import_file": import_file},
            follow=True,
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertEqual(Bookmark.objects.count(), 1)

    def test_full_roundtrip_via_views(self):
        """Test complete round-trip through the backup and restore views."""
        # Setup data
        tag = self.setup_tag(name="python")
        self.setup_bookmark(
            url="https://python.org",
            title="Python",
            description="Programming language",
            notes="Great!",
            unread=True,
            shared=True,
            tags=[tag],
        )
        self.setup_bundle(name="Dev", search="dev", order=1)

        # Download backup
        response = self.client.get(reverse("linkding:settings.backup"), follow=True)
        backup_content = response.content

        # Clear data
        Bookmark.objects.filter(owner=self.user).delete()
        BookmarkBundle.objects.filter(owner=self.user).delete()
        Tag.objects.filter(owner=self.user).delete()
        self.user.profile.theme = "auto"
        self.user.profile.save()

        self.assertEqual(Bookmark.objects.count(), 0)

        # Upload restore
        upload_file = SimpleUploadedFile(
            "backup.json.gz", backup_content, content_type="application/gzip"
        )
        response = self.client.post(
            reverse("linkding:settings.restore"),
            {"backup_file": upload_file, "restore_mode": "replace"},
            follow=True,
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertEqual(Bookmark.objects.count(), 1)
        self.assertEqual(Tag.objects.count(), 1)
        self.assertEqual(BookmarkBundle.objects.count(), 1)

        bm = Bookmark.objects.get(url="https://python.org")
        self.assertEqual(bm.title, "Python")
        self.assertEqual(bm.notes, "Great!")
        self.assertTrue(bm.unread)
        self.assertTrue(bm.shared)
        self.assertEqual(list(bm.tags.values_list("name", flat=True)), ["python"])
