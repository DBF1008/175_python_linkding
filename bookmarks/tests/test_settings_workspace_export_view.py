import datetime
import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from bookmarks.tests.helpers import BookmarkFactoryMixin


class SettingsWorkspaceExportViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def assertFormErrorHint(self, response, text: str):
        self.assertContains(response, '<div class="has-error">')
        self.assertContains(response, text)

    def test_should_export_successfully(self):
        self.setup_bookmark(notes="some notes", unread=True, tags=[self.setup_tag()])
        self.setup_bookmark(is_archived=True)
        self.setup_bundle(name="My Bundle")

        response = self.client.get(
            reverse("linkding:settings.backup_export"), follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["content-type"], "application/json")

        data = json.loads(response.content.decode("utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["bookmarks"]), 2)
        self.assertEqual(len(data["bundles"]), 1)

    def test_should_only_export_user_data(self):
        other_user = self.setup_user()
        self.setup_bookmark(url="https://example.com/mine")
        self.setup_bookmark(url="https://example.com/theirs", user=other_user)

        response = self.client.get(
            reverse("linkding:settings.backup_export"), follow=True
        )

        text = response.content.decode("utf-8")
        self.assertIn("https://example.com/mine", text)
        self.assertNotIn("https://example.com/theirs", text)

    def test_should_check_authentication(self):
        self.client.logout()
        response = self.client.get(
            reverse("linkding:settings.backup_export"), follow=True
        )

        self.assertRedirects(
            response,
            reverse("login") + "?next=" + reverse("linkding:settings.backup_export"),
        )

    def test_should_show_hint_when_export_raises_error(self):
        with patch("bookmarks.services.workspace.export_workspace_json") as mock_export:
            mock_export.side_effect = Exception("Nope")
            response = self.client.get(
                reverse("linkding:settings.backup_export"), follow=True
            )

            self.assertTemplateUsed(response, "settings/general.html")
            self.assertFormErrorHint(
                response, "An error occurred during backup export."
            )

    def test_filename_includes_date_and_time(self):
        self.setup_bookmark()

        fixed_time = datetime.datetime(2023, 5, 15, 14, 30, 45, tzinfo=datetime.UTC)
        with patch("bookmarks.views.settings.timezone.now", return_value=fixed_time):
            response = self.client.get(
                reverse("linkding:settings.backup_export"), follow=True
            )

        self.assertEqual(response.status_code, 200)
        expected_filename = (
            'attachment; filename="linkding_backup_2023-05-15_14-30-45.json"'
        )
        self.assertEqual(response["Content-Disposition"], expected_filename)
