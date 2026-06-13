import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Bookmark, BookmarkBundle
from bookmarks.tests.helpers import BookmarkFactoryMixin, disable_logging


def bookmark_entry(url, **overrides):
    entry = {
        "url": url,
        "title": "Title",
        "description": "",
        "notes": "",
        "unread": False,
        "is_archived": False,
        "shared": False,
        "web_archive_snapshot_url": "",
        "date_added": "2023-01-01T00:00:00+00:00",
        "date_modified": "2023-01-01T00:00:00+00:00",
        "date_accessed": None,
        "tag_names": [],
    }
    entry.update(overrides)
    return entry


def bundle_entry(name, **overrides):
    entry = {
        "name": name,
        "search": "",
        "any_tags": "",
        "all_tags": "",
        "excluded_tags": "",
        "filter_unread": BookmarkBundle.FILTER_STATE_OFF,
        "filter_shared": BookmarkBundle.FILTER_STATE_OFF,
        "order": 0,
        "date_created": None,
        "date_modified": None,
    }
    entry.update(overrides)
    return entry


def upload(payload, name="backup.json"):
    content = json.dumps(payload).encode("utf-8")
    return SimpleUploadedFile(name, content, content_type="application/json")


class SettingsWorkspaceImportViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def assertSuccessMessage(self, response, message: str):
        self.assertInHTML(
            f'<div class="toast toast-success mb-4">{message}</div>',
            response.content.decode("utf-8"),
        )

    def assertNoSuccessMessage(self, response):
        self.assertNotContains(response, '<div class="toast toast-success mb-4">')

    def assertErrorMessage(self, response, message: str):
        self.assertInHTML(
            f'<div class="toast toast-error mb-4">{message}</div>',
            response.content.decode("utf-8"),
        )

    def test_should_import_successfully(self):
        payload = {
            "version": 1,
            "tags": [],
            "bundles": [bundle_entry("B1")],
            "bookmarks": [
                bookmark_entry("https://example.com/1", title="One"),
                bookmark_entry("https://example.com/2", title="Two"),
            ],
        }

        response = self.client.post(
            reverse("linkding:settings.backup_import"),
            {"backup_file": upload(payload)},
            follow=True,
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertSuccessMessage(
            response,
            "Backup restored: 2 bookmarks added, 0 updated, 1 bundles added, "
            "0 updated.",
        )
        self.assertEqual(Bookmark.objects.count(), 2)
        self.assertEqual(BookmarkBundle.objects.count(), 1)

    def test_should_merge_into_existing_workspace(self):
        existing = self.setup_bookmark(url="https://example.com/1", title="Old")

        payload = {
            "version": 1,
            "tags": [],
            "bundles": [],
            "bookmarks": [
                bookmark_entry("https://example.com/1", title="New"),
                bookmark_entry("https://example.com/2", title="Added"),
            ],
        }

        response = self.client.post(
            reverse("linkding:settings.backup_import"),
            {"backup_file": upload(payload)},
            follow=True,
        )

        self.assertSuccessMessage(
            response,
            "Backup restored: 1 bookmarks added, 1 updated, 0 bundles added, "
            "0 updated.",
        )
        existing.refresh_from_db()
        self.assertEqual(existing.title, "New")
        self.assertEqual(Bookmark.objects.count(), 2)

    def test_should_check_authentication(self):
        self.client.logout()
        response = self.client.get(
            reverse("linkding:settings.backup_import"), follow=True
        )

        self.assertRedirects(
            response,
            reverse("login") + "?next=" + reverse("linkding:settings.backup_import"),
        )

    def test_should_show_hint_if_there_is_no_file(self):
        response = self.client.post(
            reverse("linkding:settings.backup_import"), follow=True
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertNoSuccessMessage(response)
        self.assertErrorMessage(response, "Please select a file to import.")

    @disable_logging
    def test_should_show_hint_if_file_is_not_valid(self):
        bad_file = SimpleUploadedFile(
            "backup.json",
            b"this is not a valid backup",
            content_type="application/json",
        )

        response = self.client.post(
            reverse("linkding:settings.backup_import"),
            {"backup_file": bad_file},
            follow=True,
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertNoSuccessMessage(response)
        self.assertErrorMessage(
            response, "An error occurred while restoring the backup."
        )

    @disable_logging
    def test_should_show_hint_if_version_unsupported(self):
        payload = {"version": 999, "tags": [], "bundles": [], "bookmarks": []}

        response = self.client.post(
            reverse("linkding:settings.backup_import"),
            {"backup_file": upload(payload)},
            follow=True,
        )

        self.assertNoSuccessMessage(response)
        self.assertErrorMessage(
            response, "An error occurred while restoring the backup."
        )
