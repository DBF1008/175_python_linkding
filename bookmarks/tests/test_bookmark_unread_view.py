from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookmarks.models import Bookmark, BookmarkSearch, UserProfile
from bookmarks.tests.helpers import (
    BookmarkFactoryMixin,
    BookmarkListTestMixin,
    HtmlTestMixin,
    TagCloudTestMixin,
)


class BookmarkUnreadViewTestCase(
    TestCase, BookmarkFactoryMixin, BookmarkListTestMixin, TagCloudTestMixin, HtmlTestMixin
):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def assertBulkActionForm(self, response, url: str):
        soup = self.make_soup(response.content.decode())
        form = soup.select_one("form.bookmark-actions")
        self.assertIsNotNone(form)
        self.assertEqual(form.attrs["action"], url)

    # ----------------------------------------------------------------
    # Category A: Unread filtering
    # ----------------------------------------------------------------

    def test_should_list_only_unread_unarchived_bookmarks(self):
        visible = self.setup_numbered_bookmarks(3, unread=True)
        read_bookmarks = self.setup_numbered_bookmarks(2, prefix="read")
        archived_unread = self.setup_numbered_bookmarks(
            2, prefix="archived", unread=True, archived=True
        )

        response = self.client.get(reverse("linkding:bookmarks.unread"))

        self.assertVisibleBookmarks(response, visible)
        self.assertInvisibleBookmarks(response, read_bookmarks + archived_unread)

    def test_should_not_show_other_users_unread_bookmarks(self):
        other_user = User.objects.create_user(
            "otheruser", "other@example.com", "password123"
        )
        my_unread = self.setup_numbered_bookmarks(3, unread=True)
        other_unread = self.setup_numbered_bookmarks(
            2, prefix="other", unread=True, user=other_user
        )

        response = self.client.get(reverse("linkding:bookmarks.unread"))

        self.assertVisibleBookmarks(response, my_unread)
        self.assertInvisibleBookmarks(response, other_unread)

    def test_should_list_bookmarks_matching_query(self):
        visible = self.setup_numbered_bookmarks(3, prefix="foo", unread=True)
        invisible = self.setup_numbered_bookmarks(3, prefix="bar", unread=True)

        response = self.client.get(
            reverse("linkding:bookmarks.unread") + "?q=foo"
        )

        self.assertVisibleBookmarks(response, visible)
        self.assertInvisibleBookmarks(response, invisible)

    def test_should_list_tags_for_unread_bookmarks_only(self):
        visible_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, unread=True, tag_prefix="visible"
        )
        read_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, prefix="read", tag_prefix="read"
        )
        archived_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, prefix="archived", archived=True, tag_prefix="archived"
        )

        visible_tags = self.get_tags_from_bookmarks(visible_bookmarks)
        invisible_tags = self.get_tags_from_bookmarks(
            read_bookmarks + archived_bookmarks
        )

        response = self.client.get(reverse("linkding:bookmarks.unread"))

        self.assertVisibleTags(response, visible_tags)
        self.assertInvisibleTags(response, invisible_tags)

    def test_should_have_correct_page_title(self):
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        html = response.content.decode()
        self.assertIn("Reading Queue", html)

    # ----------------------------------------------------------------
    # Category B: Time-based filtering
    # ----------------------------------------------------------------

    def _create_unread_bookmarks(self, count, prefix, added):
        """Helper to create unread bookmarks with a specific added date."""
        bookmarks = []
        for i in range(1, count + 1):
            bm = self.setup_bookmark(
                title=f"{prefix} {i}",
                url=f"https://example.com/{prefix}/{i}",
                unread=True,
                added=added,
            )
            bookmarks.append(bm)
        return bookmarks

    def test_time_filter_today(self):
        now = timezone.now()
        today = now
        yesterday = now - timedelta(days=1)

        today_bookmarks = self._create_unread_bookmarks(3, "today", today)
        yesterday_bookmarks = self._create_unread_bookmarks(2, "yesterday", yesterday)

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        response = self.client.get(
            reverse("linkding:bookmarks.unread"),
            {"added_since": today_start.isoformat()},
        )

        self.assertVisibleBookmarks(response, today_bookmarks)
        self.assertInvisibleBookmarks(response, yesterday_bookmarks)

    def test_time_filter_this_week(self):
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        last_week = week_start - timedelta(days=3)

        this_week = self._create_unread_bookmarks(3, "thisweek", now)
        last_week_bm = self._create_unread_bookmarks(2, "lastweek", last_week)

        response = self.client.get(
            reverse("linkding:bookmarks.unread"),
            {"added_since": week_start.isoformat()},
        )

        self.assertVisibleBookmarks(response, this_week)
        self.assertInvisibleBookmarks(response, last_week_bm)

    def test_time_filter_this_month(self):
        now = timezone.now()
        month_start = now.replace(hour=0, minute=0, second=0, microsecond=0, day=1)
        last_month = month_start - timedelta(days=15)

        this_month = self._create_unread_bookmarks(3, "thismonth", now)
        last_month_bm = self._create_unread_bookmarks(2, "lastmonth", last_month)

        response = self.client.get(
            reverse("linkding:bookmarks.unread"),
            {"added_since": month_start.isoformat()},
        )

        self.assertVisibleBookmarks(response, this_month)
        self.assertInvisibleBookmarks(response, last_month_bm)

    def test_time_filter_older(self):
        now = timezone.now()
        month_start = now.replace(hour=0, minute=0, second=0, microsecond=0, day=1)
        old_date = month_start - timedelta(days=30)

        this_month = self._create_unread_bookmarks(3, "thismonth", now)
        older = self._create_unread_bookmarks(2, "older", old_date)

        response = self.client.get(
            reverse("linkding:bookmarks.unread") + "?added_since=__older__"
        )

        self.assertVisibleBookmarks(response, older)
        self.assertInvisibleBookmarks(response, this_month)

    def test_time_filter_counts_in_context(self):
        now = timezone.now()
        month_start = now.replace(hour=0, minute=0, second=0, microsecond=0, day=1)
        old_date = month_start - timedelta(days=30)

        # 3 from this month + 2 older = 5 total
        self._create_unread_bookmarks(3, "thismonth", now)
        self._create_unread_bookmarks(2, "older", old_date)

        response = self.client.get(reverse("linkding:bookmarks.unread"))
        bookmark_list = response.context["bookmark_list"]

        time_filters = {f["label"]: f for f in bookmark_list.time_filters}
        self.assertEqual(time_filters["All"]["count"], 5)
        self.assertEqual(time_filters["This month"]["count"], 3)
        self.assertEqual(time_filters["Older"]["count"], 2)

    def test_time_filter_active_state(self):
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        self._create_unread_bookmarks(3, "test", now)

        response = self.client.get(
            reverse("linkding:bookmarks.unread"),
            {"added_since": today_start.isoformat()},
        )
        bookmark_list = response.context["bookmark_list"]

        time_filters = {f["label"]: f for f in bookmark_list.time_filters}
        self.assertTrue(time_filters["Today"]["active"])
        self.assertFalse(time_filters["All"]["active"])
        self.assertFalse(time_filters["This week"]["active"])
        self.assertFalse(time_filters["This month"]["active"])
        self.assertFalse(time_filters["Older"]["active"])

    # ----------------------------------------------------------------
    # Category C: Batch marking / bulk actions
    # ----------------------------------------------------------------

    def test_bulk_mark_as_read(self):
        b1 = self.setup_bookmark(unread=True)
        b2 = self.setup_bookmark(unread=True)
        b3 = self.setup_bookmark(unread=True)

        self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {
                "bulk_action": ["bulk_read"],
                "bulk_execute": [""],
                "bookmark_id": [str(b1.id), str(b2.id), str(b3.id)],
            },
        )

        self.assertFalse(Bookmark.objects.get(id=b1.id).unread)
        self.assertFalse(Bookmark.objects.get(id=b2.id).unread)
        self.assertFalse(Bookmark.objects.get(id=b3.id).unread)

    def test_bulk_archive_from_reading_queue(self):
        b1 = self.setup_bookmark(unread=True)
        b2 = self.setup_bookmark(unread=True)
        b3 = self.setup_bookmark(unread=True)

        self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {
                "bulk_action": ["bulk_archive"],
                "bulk_execute": [""],
                "bookmark_id": [str(b1.id), str(b2.id), str(b3.id)],
            },
        )

        self.assertTrue(Bookmark.objects.get(id=b1.id).is_archived)
        self.assertTrue(Bookmark.objects.get(id=b2.id).is_archived)
        self.assertTrue(Bookmark.objects.get(id=b3.id).is_archived)

        # Archived bookmarks should no longer appear in reading queue
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        self.assertInvisibleBookmarks(response, [b1, b2, b3])

    def test_bulk_delete_from_reading_queue(self):
        b1 = self.setup_bookmark(unread=True)
        b2 = self.setup_bookmark(unread=True)
        b3 = self.setup_bookmark(unread=True)

        self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {
                "bulk_action": ["bulk_delete"],
                "bulk_execute": [""],
                "bookmark_id": [str(b1.id), str(b2.id), str(b3.id)],
            },
        )

        self.assertIsNone(Bookmark.objects.filter(id=b1.id).first())
        self.assertIsNone(Bookmark.objects.filter(id=b2.id).first())
        self.assertIsNone(Bookmark.objects.filter(id=b3.id).first())

    def test_bulk_tag_from_reading_queue(self):
        b1 = self.setup_bookmark(unread=True)
        b2 = self.setup_bookmark(unread=True)
        tag = self.setup_tag()

        self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {
                "bulk_action": ["bulk_tag"],
                "bulk_execute": [""],
                "bulk_tag_string": [tag.name],
                "bookmark_id": [str(b1.id), str(b2.id)],
            },
        )

        b1.refresh_from_db()
        b2.refresh_from_db()
        self.assertIn(tag, b1.tags.all())
        self.assertIn(tag, b2.tags.all())

    def test_bulk_select_across_pages(self):
        unread_bookmarks = self.setup_numbered_bookmarks(
            40, unread=True
        )

        self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {
                "bulk_action": ["bulk_read"],
                "bulk_execute": [""],
                "bulk_select_across": ["on"],
            },
        )

        for bm in unread_bookmarks:
            bm.refresh_from_db()
            self.assertFalse(bm.unread)

    def test_can_only_bulk_act_on_own_bookmarks(self):
        other_user = User.objects.create_user(
            "otheruser", "other@example.com", "password123"
        )
        b1 = self.setup_bookmark(unread=True, user=other_user)
        b2 = self.setup_bookmark(unread=True, user=other_user)

        self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {
                "bulk_action": ["bulk_read"],
                "bulk_execute": [""],
                "bookmark_id": [str(b1.id), str(b2.id)],
            },
        )

        self.assertTrue(Bookmark.objects.get(id=b1.id).unread)
        self.assertTrue(Bookmark.objects.get(id=b2.id).unread)

    # ----------------------------------------------------------------
    # Category D: Archive interaction (single actions)
    # ----------------------------------------------------------------

    def test_single_mark_as_read_from_reading_queue(self):
        bookmark = self.setup_bookmark(unread=True)

        self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {"mark_as_read": str(bookmark.id)},
        )

        bookmark.refresh_from_db()
        self.assertFalse(bookmark.unread)

    def test_single_archive_from_reading_queue(self):
        bookmark = self.setup_bookmark(unread=True)

        self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {"archive": str(bookmark.id)},
        )

        bookmark.refresh_from_db()
        self.assertTrue(bookmark.is_archived)

    def test_archived_unread_bookmarks_do_not_appear(self):
        visible = self.setup_numbered_bookmarks(3, unread=True)
        archived_unread = [
            self.setup_bookmark(unread=True, is_archived=True),
            self.setup_bookmark(unread=True, is_archived=True),
        ]

        response = self.client.get(reverse("linkding:bookmarks.unread"))

        self.assertVisibleBookmarks(response, visible)
        self.assertInvisibleBookmarks(response, archived_unread)

    # ----------------------------------------------------------------
    # Category E: Pagination
    # ----------------------------------------------------------------

    def test_pagination_with_many_unread_bookmarks(self):
        user = self.get_or_create_test_user()
        user.profile.items_per_page = 10
        user.profile.save()

        unread_bookmarks = self.setup_numbered_bookmarks(25, unread=True)

        response = self.client.get(reverse("linkding:bookmarks.unread"))
        soup = self.make_soup(response.content.decode())
        items = soup.select("ul.bookmark-list > li")
        self.assertEqual(10, len(items))

        response = self.client.get(reverse("linkding:bookmarks.unread") + "?page=2")
        soup = self.make_soup(response.content.decode())
        items = soup.select("ul.bookmark-list > li")
        self.assertEqual(10, len(items))

        response = self.client.get(reverse("linkding:bookmarks.unread") + "?page=3")
        soup = self.make_soup(response.content.decode())
        items = soup.select("ul.bookmark-list > li")
        self.assertEqual(5, len(items))

    def test_pagination_preserves_time_filter(self):
        user = self.get_or_create_test_user()
        user.profile.items_per_page = 10
        user.profile.save()

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        self._create_unread_bookmarks(25, "test", now)

        response = self.client.get(
            reverse("linkding:bookmarks.unread"),
            {"added_since": today_start.isoformat(), "page": "2"},
        )
        html = response.content.decode()

        # The pagination links should preserve the added_since param
        self.assertIn("added_since=", html)

    def test_empty_reading_queue(self):
        # No unread bookmarks at all
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        html = response.content.decode()
        self.assertInHTML(
            '<p class="empty-title h5">Your reading queue is empty</p>', html
        )

    def test_empty_reading_queue_after_filtering(self):
        # Only read bookmarks exist
        self.setup_numbered_bookmarks(3)
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        html = response.content.decode()
        self.assertInHTML(
            '<p class="empty-title h5">Your reading queue is empty</p>', html
        )

    # ----------------------------------------------------------------
    # Category F: Navigation & Integration
    # ----------------------------------------------------------------

    def test_nav_menu_contains_reading_queue_link(self):
        response = self.client.get(reverse("linkding:bookmarks.index"))
        html = response.content.decode()
        self.assertInHTML(
            f'<a href="{reverse("linkding:bookmarks.unread")}" class="menu-link">Reading Queue</a>',
            html,
        )

    def test_action_form_points_to_unread_action(self):
        self.setup_bookmark(unread=True)
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        action_url = reverse("linkding:bookmarks.unread.action")
        self.assertBulkActionForm(response, action_url)

    def test_return_url_points_to_reading_queue(self):
        bookmark = self.setup_bookmark(unread=True)
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        html = response.content.decode()
        return_url = reverse("linkding:bookmarks.unread")
        self.assertIn(f"return_url={return_url}", html)

    def test_unread_action_redirects_to_unread_view(self):
        bookmark = self.setup_bookmark(unread=True)
        response = self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            {"mark_as_read": str(bookmark.id)},
        )
        self.assertRedirects(
            response, reverse("linkding:bookmarks.unread")
        )

    def test_turbo_stream_update_after_action(self):
        self.setup_numbered_bookmarks(3, unread=True)
        response = self.client.post(
            reverse("linkding:bookmarks.unread.action"),
            HTTP_ACCEPT="text/vnd.turbo-stream.html",
        )

        self.assertEqual(response.status_code, 200)
        soup = self.make_soup(response.content.decode())
        self.assertIsNotNone(
            soup.select_one(
                "turbo-stream[action='update'][target='bookmark-list-container']"
            )
        )
        self.assertIsNotNone(
            soup.select_one(
                "turbo-stream[action='update'][target='tag-cloud-container']"
            )
        )

    # ----------------------------------------------------------------
    # Category G: Disabled actions in bulk edit bar
    # ----------------------------------------------------------------

    def test_bulk_unarchive_is_disabled(self):
        self.setup_bookmark(unread=True)
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        soup = self.make_soup(response.content.decode())
        select = soup.select_one("select[name='bulk_action']")
        options = [opt.attrs["value"] for opt in select.select("option")]
        self.assertNotIn("bulk_unarchive", options)

    def test_bulk_unread_is_disabled(self):
        self.setup_bookmark(unread=True)
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        soup = self.make_soup(response.content.decode())
        select = soup.select_one("select[name='bulk_action']")
        options = [opt.attrs["value"] for opt in select.select("option")]
        self.assertNotIn("bulk_unread", options)

    def test_bulk_read_is_enabled(self):
        self.setup_bookmark(unread=True)
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        soup = self.make_soup(response.content.decode())
        select = soup.select_one("select[name='bulk_action']")
        options = [opt.attrs["value"] for opt in select.select("option")]
        self.assertIn("bulk_read", options)

    def test_bulk_archive_is_enabled(self):
        self.setup_bookmark(unread=True)
        response = self.client.get(reverse("linkding:bookmarks.unread"))
        soup = self.make_soup(response.content.decode())
        select = soup.select_one("select[name='bulk_action']")
        options = [opt.attrs["value"] for opt in select.select("option")]
        self.assertIn("bulk_archive", options)
