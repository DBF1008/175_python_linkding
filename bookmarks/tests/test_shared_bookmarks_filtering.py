from django.test import TestCase
from django.urls import reverse

from bookmarks.models import FeedToken
from bookmarks.tests.helpers import BookmarkFactoryMixin, BookmarkListTestMixin


class SharedBookmarksFilteringTestCase(
    TestCase, BookmarkFactoryMixin, BookmarkListTestMixin
):
    """Regression tests for shared bookmark filtering and feed enhancements."""

    def setUp(self):
        self.user1 = self.setup_user(
            name="sharer1", enable_sharing=True, enable_public_sharing=True
        )
        self.user2 = self.setup_user(
            name="sharer2", enable_sharing=True, enable_public_sharing=True
        )
        self.viewer = self.setup_user(name="viewer")
        self.tag_python = self.setup_tag(name="python", user=self.user1)
        self.tag_django = self.setup_tag(name="django", user=self.user1)
        self.tag_rust = self.setup_tag(name="rust", user=self.user2)

    def _setup_shared_bookmarks(self):
        """Create a standard set of shared bookmarks for testing."""
        bm1 = self.setup_bookmark(
            title="Python Guide",
            url="https://example.com/python",
            shared=True,
            unread=True,
            user=self.user1,
            tags=[self.tag_python],
        )
        bm2 = self.setup_bookmark(
            title="Django Tutorial",
            url="https://example.com/django",
            shared=True,
            unread=False,
            user=self.user1,
            tags=[self.tag_python, self.tag_django],
        )
        bm3 = self.setup_bookmark(
            title="Rust Lang",
            url="https://example.com/rust",
            shared=True,
            unread=True,
            user=self.user2,
            tags=[self.tag_rust],
        )
        bm4 = self.setup_bookmark(
            title="Python Django Combo",
            url="https://example.com/python-django",
            shared=True,
            unread=False,
            user=self.user1,
            tags=[self.tag_python, self.tag_django],
        )
        return [bm1, bm2, bm3, bm4]

    # -------------------------------------------------------
    # Shared page: unread filter
    # -------------------------------------------------------

    def test_shared_unread_filter_logged_in(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)

        # Without filter: all shared bookmarks visible
        response = self.client.get(reverse("linkding:bookmarks.shared"))
        self.assertVisibleBookmarks(response, bookmarks)

        # With unread=yes: only unread bookmarks
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?unread=yes"
        )
        self.assertVisibleBookmarks(response, [bookmarks[0], bookmarks[2]])
        self.assertInvisibleBookmarks(response, [bookmarks[1], bookmarks[3]])

        # With unread=no: only read bookmarks
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?unread=no"
        )
        self.assertVisibleBookmarks(response, [bookmarks[1], bookmarks[3]])
        self.assertInvisibleBookmarks(response, [bookmarks[0], bookmarks[2]])

    def test_shared_unread_filter_anonymous(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.logout()

        # Anonymous user with unread=yes
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?unread=yes"
        )
        self.assertEqual(response.status_code, 200)
        self.assertVisibleBookmarks(response, [bookmarks[0], bookmarks[2]])
        self.assertInvisibleBookmarks(response, [bookmarks[1], bookmarks[3]])

    # -------------------------------------------------------
    # Shared page: tag URL parameter
    # -------------------------------------------------------

    def test_shared_tag_param_single(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)

        # Filter by python tag: bm1, bm2, bm4
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?tag=python"
        )
        self.assertEqual(response.status_code, 200)
        self.assertVisibleBookmarks(
            response, [bookmarks[0], bookmarks[1], bookmarks[3]]
        )
        self.assertInvisibleBookmarks(response, [bookmarks[2]])

    def test_shared_tag_param_multiple_and_logic(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)

        # Filter by python AND django: bm2, bm4
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?tag=python&tag=django"
        )
        self.assertEqual(response.status_code, 200)
        self.assertVisibleBookmarks(response, [bookmarks[1], bookmarks[3]])
        self.assertInvisibleBookmarks(response, [bookmarks[0], bookmarks[2]])

    def test_shared_tag_param_case_insensitive(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)

        # Case insensitive: Python should match python
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?tag=Python"
        )
        self.assertEqual(response.status_code, 200)
        self.assertVisibleBookmarks(
            response, [bookmarks[0], bookmarks[1], bookmarks[3]]
        )

    def test_shared_tag_param_anonymous(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.logout()

        # Anonymous user with tag filter
        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?tag=rust"
        )
        self.assertEqual(response.status_code, 200)
        self.assertVisibleBookmarks(response, [bookmarks[2]])
        self.assertInvisibleBookmarks(
            response, [bookmarks[0], bookmarks[1], bookmarks[3]]
        )

    # -------------------------------------------------------
    # Shared page: combined filters
    # -------------------------------------------------------

    def test_shared_combined_user_tag_unread(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)

        # user1 + python + unread=yes → only bm1
        response = self.client.get(
            reverse("linkding:bookmarks.shared")
            + f"?user={self.user1.username}&tag=python&unread=yes"
        )
        self.assertEqual(response.status_code, 200)
        self.assertVisibleBookmarks(response, [bookmarks[0]])
        self.assertInvisibleBookmarks(
            response, [bookmarks[1], bookmarks[2], bookmarks[3]]
        )

    # -------------------------------------------------------
    # Public shared feed: tag filter
    # -------------------------------------------------------

    def test_public_shared_feed_tag_filter(self):
        bookmarks = self._setup_shared_bookmarks()

        response = self.client.get(
            reverse("linkding:feeds.public_shared") + "?tag=python"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=3)
        self.assertContains(
            response, f"<guid>{bookmarks[0].url}</guid>", count=1
        )
        self.assertContains(
            response, f"<guid>{bookmarks[1].url}</guid>", count=1
        )
        self.assertContains(
            response, f"<guid>{bookmarks[3].url}</guid>", count=1
        )
        self.assertContains(
            response, f"<guid>{bookmarks[2].url}</guid>", count=0
        )

    def test_public_shared_feed_multiple_tags_and_logic(self):
        bookmarks = self._setup_shared_bookmarks()

        # python AND django → bm2, bm4
        response = self.client.get(
            reverse("linkding:feeds.public_shared") + "?tag=python&tag=django"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=2)
        self.assertContains(
            response, f"<guid>{bookmarks[1].url}</guid>", count=1
        )
        self.assertContains(
            response, f"<guid>{bookmarks[3].url}</guid>", count=1
        )

    # -------------------------------------------------------
    # Public shared feed: user filter
    # -------------------------------------------------------

    def test_public_shared_feed_user_filter(self):
        bookmarks = self._setup_shared_bookmarks()

        # Filter by user1 → bm1, bm2, bm4
        response = self.client.get(
            reverse("linkding:feeds.public_shared")
            + f"?user={self.user1.username}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=3)
        self.assertContains(
            response, f"<guid>{bookmarks[0].url}</guid>", count=1
        )
        self.assertContains(
            response, f"<guid>{bookmarks[1].url}</guid>", count=1
        )
        self.assertContains(
            response, f"<guid>{bookmarks[3].url}</guid>", count=1
        )
        self.assertContains(
            response, f"<guid>{bookmarks[2].url}</guid>", count=0
        )

    def test_public_shared_feed_nonexistent_user(self):
        self._setup_shared_bookmarks()

        # Non-existent user → no filter applied, returns all
        response = self.client.get(
            reverse("linkding:feeds.public_shared") + "?user=ghost"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=4)

    # -------------------------------------------------------
    # Public shared feed: sort
    # -------------------------------------------------------

    def test_public_shared_feed_sort_title_asc(self):
        self._setup_shared_bookmarks()

        response = self.client.get(
            reverse("linkding:feeds.public_shared") + "?sort=title_asc"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=4)

        # Verify order: Django Tutorial, Python Django Combo, Python Guide, Rust Lang
        content = response.content.decode()
        pos_django = content.index("<title>Django Tutorial</title>")
        pos_combo = content.index("<title>Python Django Combo</title>")
        pos_python = content.index("<title>Python Guide</title>")
        pos_rust = content.index("<title>Rust Lang</title>")
        self.assertLess(pos_django, pos_combo)
        self.assertLess(pos_combo, pos_python)
        self.assertLess(pos_python, pos_rust)

    # -------------------------------------------------------
    # Auth shared feed: tag/user filters
    # -------------------------------------------------------

    def test_shared_feed_tag_filter(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)
        token = FeedToken.objects.get_or_create(user=self.viewer)[0]

        response = self.client.get(
            reverse("linkding:feeds.shared", args=[token.key]) + "?tag=python"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=3)
        self.assertContains(
            response, f"<guid>{bookmarks[0].url}</guid>", count=1
        )

    def test_shared_feed_user_filter(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)
        token = FeedToken.objects.get_or_create(user=self.viewer)[0]

        response = self.client.get(
            reverse("linkding:feeds.shared", args=[token.key])
            + f"?user={self.user2.username}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=1)
        self.assertContains(
            response, f"<guid>{bookmarks[2].url}</guid>", count=1
        )

    # -------------------------------------------------------
    # Feed: combined filters
    # -------------------------------------------------------

    def test_feed_combined_tag_user_sort_unread(self):
        bookmarks = self._setup_shared_bookmarks()

        # user1 + python + unread=yes + sort=title_asc → only bm1 (Python Guide)
        response = self.client.get(
            reverse("linkding:feeds.public_shared")
            + f"?tag=python&user={self.user1.username}&unread=yes&sort=title_asc"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=1)
        self.assertContains(
            response, f"<guid>{bookmarks[0].url}</guid>", count=1
        )

    def test_feed_tag_with_q_interaction(self):
        """tag param and q=#tag should AND together."""
        bookmarks = self._setup_shared_bookmarks()

        # q=#django AND tag=python → bm2, bm4 (both have python+django)
        response = self.client.get(
            reverse("linkding:feeds.public_shared")
            + "?tag=python&q=%23django"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=2)
        self.assertContains(
            response, f"<guid>{bookmarks[1].url}</guid>", count=1
        )
        self.assertContains(
            response, f"<guid>{bookmarks[3].url}</guid>", count=1
        )

    # -------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------

    def test_feed_empty_tag_ignored(self):
        self._setup_shared_bookmarks()

        # Empty tag param should be ignored
        response = self.client.get(
            reverse("linkding:feeds.public_shared") + "?tag="
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=4)

    def test_feed_duplicate_tags_deduped(self):
        bookmarks = self._setup_shared_bookmarks()

        # Duplicate tag params → treated as single filter
        response = self.client.get(
            reverse("linkding:feeds.public_shared") + "?tag=python&tag=python"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<item>", count=3)

    def test_shared_page_empty_tag_ignored(self):
        bookmarks = self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?tag="
        )
        self.assertEqual(response.status_code, 200)
        # Should show all bookmarks, empty tag is ignored
        self.assertVisibleBookmarks(response, bookmarks)

    def test_shared_page_nonexistent_tag_returns_empty(self):
        self._setup_shared_bookmarks()
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("linkding:bookmarks.shared") + "?tag=nonexistent"
        )
        self.assertEqual(response.status_code, 200)
        # No bookmarks should match
        soup = self._make_soup(response.content.decode())
        bookmark_items = soup.select("ul.bookmark-list > li")
        self.assertEqual(len(bookmark_items), 0)

    def _make_soup(self, html):
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, features="html.parser")
