import urllib.parse

from django.test import TestCase
from django.urls import reverse

from bookmarks.models import BookmarkSavedSearch, BookmarkSearch
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class SavedSearchesViewTestCase(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def action_url(self):
        return reverse("linkding:saved_searches.action")

    # --- create ---------------------------------------------------------------

    def test_create_saved_search(self):
        form_data = {
            "create": "",
            "name": "My filter",
            "q": "python",
            "unread": BookmarkSearch.FILTER_UNREAD_YES,
            "return_url": "/bookmarks",
        }

        response = self.client.post(self.action_url(), form_data)

        self.assertEqual(BookmarkSavedSearch.objects.count(), 1)
        saved_search = BookmarkSavedSearch.objects.first()
        self.assertEqual(saved_search.owner, self.user)
        self.assertEqual(saved_search.name, "My filter")

        # The captured query is page-agnostic and contains the modified params only
        parsed = urllib.parse.parse_qs(saved_search.query)
        self.assertEqual(parsed.get("q"), ["python"])
        self.assertEqual(parsed.get("unread"), ["yes"])
        self.assertNotIn("return_url", parsed)
        self.assertNotIn("create", parsed)

        self.assertRedirects(response, "/bookmarks", fetch_redirect_response=False)

    def test_create_saved_search_with_empty_name_returns_422(self):
        response = self.client.post(
            self.action_url(), {"create": "", "name": "", "q": "python"}
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(BookmarkSavedSearch.objects.count(), 0)

    def test_create_saved_search_falls_back_to_index_without_return_url(self):
        response = self.client.post(self.action_url(), {"create": "", "name": "X"})

        self.assertEqual(BookmarkSavedSearch.objects.count(), 1)
        self.assertRedirects(
            response,
            reverse("linkding:bookmarks.index"),
            fetch_redirect_response=False,
        )

    def test_create_saved_search_requires_login(self):
        self.client.logout()

        response = self.client.post(self.action_url(), {"create": "", "name": "X"})

        self.assertEqual(BookmarkSavedSearch.objects.count(), 0)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    # --- rename ---------------------------------------------------------------

    def test_rename_saved_search(self):
        saved_search = self.setup_saved_search(name="Old name", query="q=python")

        response = self.client.post(
            self.action_url(), {"rename": saved_search.id, "name": "New name"}
        )

        saved_search.refresh_from_db()
        self.assertEqual(saved_search.name, "New name")
        # Renaming must not touch the captured query
        self.assertEqual(saved_search.query, "q=python")
        self.assertRedirects(
            response,
            reverse("linkding:saved_searches.index"),
            fetch_redirect_response=False,
        )

    def test_rename_saved_search_with_empty_name_returns_422(self):
        saved_search = self.setup_saved_search(name="Old name")

        response = self.client.post(
            self.action_url(), {"rename": saved_search.id, "name": ""}
        )

        self.assertEqual(response.status_code, 422)
        saved_search.refresh_from_db()
        self.assertEqual(saved_search.name, "Old name")

    def test_rename_other_users_saved_search_returns_404(self):
        other_user = self.setup_user()
        saved_search = self.setup_saved_search(user=other_user, name="Theirs")

        response = self.client.post(
            self.action_url(), {"rename": saved_search.id, "name": "Mine"}
        )

        self.assertEqual(response.status_code, 404)
        saved_search.refresh_from_db()
        self.assertEqual(saved_search.name, "Theirs")

    # --- overwrite existing conditions ---------------------------------------

    def test_overwrite_saved_search(self):
        saved_search = self.setup_saved_search(name="Filter", query="q=old")

        form_data = {
            "overwrite": saved_search.id,
            "q": "new",
            "unread": BookmarkSearch.FILTER_UNREAD_YES,
            "return_url": "/bookmarks/archived",
        }
        response = self.client.post(self.action_url(), form_data)

        saved_search.refresh_from_db()
        parsed = urllib.parse.parse_qs(saved_search.query)
        self.assertEqual(parsed.get("q"), ["new"])
        self.assertEqual(parsed.get("unread"), ["yes"])
        # Overwrite replaces the conditions but keeps the name
        self.assertEqual(saved_search.name, "Filter")
        self.assertRedirects(
            response, "/bookmarks/archived", fetch_redirect_response=False
        )

    def test_overwrite_other_users_saved_search_returns_404(self):
        other_user = self.setup_user()
        saved_search = self.setup_saved_search(user=other_user, query="q=old")

        response = self.client.post(
            self.action_url(), {"overwrite": saved_search.id, "q": "new"}
        )

        self.assertEqual(response.status_code, 404)
        saved_search.refresh_from_db()
        self.assertEqual(saved_search.query, "q=old")

    # --- remove ---------------------------------------------------------------

    def test_remove_saved_search(self):
        saved_search = self.setup_saved_search()

        response = self.client.post(self.action_url(), {"remove": saved_search.id})

        self.assertEqual(BookmarkSavedSearch.objects.count(), 0)
        self.assertRedirects(
            response,
            reverse("linkding:saved_searches.index"),
            fetch_redirect_response=False,
        )

    def test_remove_other_users_saved_search_returns_404(self):
        other_user = self.setup_user()
        saved_search = self.setup_saved_search(user=other_user)

        response = self.client.post(self.action_url(), {"remove": saved_search.id})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(BookmarkSavedSearch.objects.count(), 1)

    # --- management page ------------------------------------------------------

    def test_index_lists_saved_searches(self):
        self.setup_saved_search(name="Alpha filter")
        self.setup_saved_search(name="Beta filter")

        response = self.client.get(reverse("linkding:saved_searches.index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha filter")
        self.assertContains(response, "Beta filter")

    def test_index_only_lists_own_saved_searches(self):
        self.setup_saved_search(name="Mine")
        other_user = self.setup_user()
        self.setup_saved_search(user=other_user, name="Theirs")

        response = self.client.get(reverse("linkding:saved_searches.index"))

        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Theirs")

    # --- cross-page reuse -----------------------------------------------------

    def test_saved_search_filter_applies_on_both_pages(self):
        # Distinct, non-overlapping titles so substring matches can't give false positives
        apple = self.setup_bookmark(unread=True, title="Apple Bookmark")
        banana = self.setup_bookmark(unread=False, title="Banana Bookmark")
        cherry = self.setup_bookmark(
            unread=True, is_archived=True, title="Cherry Bookmark"
        )
        durian = self.setup_bookmark(
            unread=False, is_archived=True, title="Durian Bookmark"
        )

        saved_search = self.setup_saved_search(name="Crosspage", query="unread=yes")

        # Apply the same saved search on the home (index) page
        index_url = reverse("linkding:bookmarks.index") + "?" + saved_search.query
        response = self.client.get(index_url)
        self.assertContains(response, apple.title)  # unread + active
        self.assertNotContains(response, banana.title)  # read filtered out
        self.assertNotContains(response, cherry.title)  # archived not on index

        # Apply the very same saved search on the archived page
        archived_url = reverse("linkding:bookmarks.archived") + "?" + saved_search.query
        response = self.client.get(archived_url)
        self.assertContains(response, cherry.title)  # unread + archived
        self.assertNotContains(response, durian.title)  # read filtered out
        self.assertNotContains(response, apple.title)  # active not on archived

    def test_saved_search_apply_links_render_on_both_pages(self):
        self.setup_saved_search(name="My filter", query="unread=yes")

        index_path = reverse("linkding:bookmarks.index")
        archived_path = reverse("linkding:bookmarks.archived")

        for page_url in [index_path, archived_path]:
            response = self.client.get(page_url)
            self.assertEqual(response.status_code, 200)
            soup = self.make_soup(response.content.decode())
            hrefs = [a.get("href") for a in soup.find_all("a")]

            # The saved search name is rendered in the sidebar section
            self.assertContains(response, "My filter")
            # Cross-page apply links to BOTH pages are present regardless of which
            # page we are currently on -> the same filter is reusable across pages
            self.assertIn(index_path + "?unread=yes", hrefs)
            self.assertIn(archived_path + "?unread=yes", hrefs)
