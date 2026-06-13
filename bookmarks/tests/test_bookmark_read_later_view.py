import urllib.parse
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bookmarks.models import BookmarkSearch, UserProfile
from bookmarks.tests.helpers import (
    BookmarkFactoryMixin,
    BookmarkListTestMixin,
    TagCloudTestMixin,
)


class BookmarkReadLaterViewTestCase(
    TestCase, BookmarkFactoryMixin, BookmarkListTestMixin, TagCloudTestMixin
):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def assertEditLink(self, response, url):
        html = response.content.decode()
        self.assertInHTML(
            f"""
            <a href="{url}">Edit</a>
        """,
            html,
        )

    def assertBulkActionForm(self, response, url: str):
        soup = self.make_soup(response.content.decode())
        form = soup.select_one("form.bookmark-actions")
        self.assertIsNotNone(form)
        self.assertEqual(form.attrs["action"], url)

    def assertBookmarkOrder(self, response, bookmarks: list):
        soup = self.make_soup(response.content.decode())
        items = soup.select("ul.bookmark-list > li")
        ids = [int(item["data-bookmark-id"]) for item in items]
        self.assertEqual(ids, [bookmark.id for bookmark in bookmarks])

    def test_should_list_unread_and_user_owned_bookmarks(self):
        other_user = User.objects.create_user(
            "otheruser", "otheruser@example.com", "password123"
        )
        visible_bookmarks = self.setup_numbered_bookmarks(3, unread=True)
        invisible_bookmarks = [
            # read bookmark is not part of the reading queue
            self.setup_bookmark(unread=False),
            # archived bookmarks leave the queue even when still unread
            self.setup_bookmark(unread=True, is_archived=True),
            # other users' unread bookmarks are never visible
            self.setup_bookmark(unread=True, user=other_user),
        ]

        response = self.client.get(reverse("linkding:bookmarks.read_later"))

        self.assertVisibleBookmarks(response, visible_bookmarks)
        self.assertInvisibleBookmarks(response, invisible_bookmarks)

    def test_should_not_list_archived_unread_bookmarks(self):
        visible_bookmarks = self.setup_numbered_bookmarks(2, unread=True)
        archived_unread_bookmarks = self.setup_numbered_bookmarks(
            2, unread=True, archived=True, prefix="archived"
        )

        response = self.client.get(reverse("linkding:bookmarks.read_later"))

        self.assertVisibleBookmarks(response, visible_bookmarks)
        self.assertInvisibleBookmarks(response, archived_unread_bookmarks)

    def test_should_list_bookmarks_matching_query(self):
        visible_bookmarks = self.setup_numbered_bookmarks(3, unread=True, prefix="foo")
        invisible_bookmarks = self.setup_numbered_bookmarks(
            3, unread=True, prefix="bar"
        )

        response = self.client.get(reverse("linkding:bookmarks.read_later") + "?q=foo")

        self.assertVisibleBookmarks(response, visible_bookmarks)
        self.assertInvisibleBookmarks(response, invisible_bookmarks)

    def test_should_list_bookmarks_matching_bundle(self):
        visible_bookmarks = self.setup_numbered_bookmarks(3, unread=True, prefix="foo")
        invisible_bookmarks = self.setup_numbered_bookmarks(
            3, unread=True, prefix="bar"
        )

        bundle = self.setup_bundle(search="foo")

        response = self.client.get(
            reverse("linkding:bookmarks.read_later") + f"?bundle={bundle.id}"
        )

        self.assertVisibleBookmarks(response, visible_bookmarks)
        self.assertInvisibleBookmarks(response, invisible_bookmarks)

    def test_should_list_tags_for_unread_and_user_owned_bookmarks(self):
        other_user = User.objects.create_user(
            "otheruser", "otheruser@example.com", "password123"
        )
        visible_bookmarks = self.setup_numbered_bookmarks(
            3, unread=True, with_tags=True
        )
        read_bookmarks = self.setup_numbered_bookmarks(
            3, unread=False, with_tags=True, tag_prefix="read"
        )
        archived_bookmarks = self.setup_numbered_bookmarks(
            3, unread=True, archived=True, with_tags=True, tag_prefix="archived"
        )
        other_user_bookmarks = self.setup_numbered_bookmarks(
            3, unread=True, with_tags=True, user=other_user, tag_prefix="otheruser"
        )

        visible_tags = self.get_tags_from_bookmarks(visible_bookmarks)
        invisible_tags = self.get_tags_from_bookmarks(
            read_bookmarks + archived_bookmarks + other_user_bookmarks
        )

        response = self.client.get(reverse("linkding:bookmarks.read_later"))

        self.assertVisibleTags(response, visible_tags)
        self.assertInvisibleTags(response, invisible_tags)

    def test_should_list_tags_for_bookmarks_matching_query(self):
        visible_bookmarks = self.setup_numbered_bookmarks(
            3, unread=True, with_tags=True, prefix="foo", tag_prefix="foo"
        )
        invisible_bookmarks = self.setup_numbered_bookmarks(
            3, unread=True, with_tags=True, prefix="bar", tag_prefix="bar"
        )

        visible_tags = self.get_tags_from_bookmarks(visible_bookmarks)
        invisible_tags = self.get_tags_from_bookmarks(invisible_bookmarks)

        response = self.client.get(reverse("linkding:bookmarks.read_later") + "?q=foo")

        self.assertVisibleTags(response, visible_tags)
        self.assertInvisibleTags(response, invisible_tags)

    def test_should_display_selected_tags_from_query(self):
        tags = [
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
        ]
        self.setup_bookmark(unread=True, tags=tags)

        response = self.client.get(
            reverse("linkding:bookmarks.read_later")
            + f"?q=%23{tags[0].name}+%23{tags[1].name}"
        )

        self.assertSelectedTags(response, [tags[0], tags[1]])

    def test_should_open_bookmarks_in_new_page_by_default(self):
        visible_bookmarks = self.setup_numbered_bookmarks(3, unread=True)

        response = self.client.get(reverse("linkding:bookmarks.read_later"))

        self.assertVisibleBookmarks(response, visible_bookmarks, "_blank")

    def test_should_open_bookmarks_in_same_page_if_specified_in_user_profile(self):
        user = self.get_or_create_test_user()
        user.profile.bookmark_link_target = UserProfile.BOOKMARK_LINK_TARGET_SELF
        user.profile.save()

        visible_bookmarks = self.setup_numbered_bookmarks(3, unread=True)

        response = self.client.get(reverse("linkding:bookmarks.read_later"))

        self.assertVisibleBookmarks(response, visible_bookmarks, "_self")

    def test_should_sort_bookmarks_oldest_first_by_default(self):
        now = timezone.now()
        oldest = self.setup_bookmark(
            unread=True, title="oldest", added=now - timedelta(days=3)
        )
        middle = self.setup_bookmark(
            unread=True, title="middle", added=now - timedelta(days=2)
        )
        newest = self.setup_bookmark(
            unread=True, title="newest", added=now - timedelta(days=1)
        )

        # Default order is oldest first to work through the backlog as a queue
        response = self.client.get(reverse("linkding:bookmarks.read_later"))
        self.assertBookmarkOrder(response, [oldest, middle, newest])

    def test_explicit_sort_overrides_oldest_first_default(self):
        now = timezone.now()
        oldest = self.setup_bookmark(
            unread=True, title="oldest", added=now - timedelta(days=3)
        )
        middle = self.setup_bookmark(
            unread=True, title="middle", added=now - timedelta(days=2)
        )
        newest = self.setup_bookmark(
            unread=True, title="newest", added=now - timedelta(days=1)
        )

        response = self.client.get(
            reverse("linkding:bookmarks.read_later") + "?sort=added_desc"
        )
        self.assertBookmarkOrder(response, [newest, middle, oldest])

    def test_should_paginate_bookmarks(self):
        profile = self.user.profile
        profile.items_per_page = 10
        profile.save()
        self.setup_numbered_bookmarks(15, unread=True)

        # page 1 shows the first page of items but reports the full queue total
        response = self.client.get(reverse("linkding:bookmarks.read_later"))
        soup = self.make_soup(response.content.decode())
        self.assertEqual(len(soup.select("ul.bookmark-list > li")), 10)
        bookmark_list = soup.select_one("ul.bookmark-list")
        self.assertEqual(bookmark_list["data-bookmarks-total"], "15")

        # page 2 shows the remaining items
        response = self.client.get(reverse("linkding:bookmarks.read_later") + "?page=2")
        soup = self.make_soup(response.content.decode())
        self.assertEqual(len(soup.select("ul.bookmark-list > li")), 5)

    def test_edit_link_return_url_respects_search_options(self):
        bookmark = self.setup_bookmark(title="foo", unread=True)
        edit_url = reverse("linkding:bookmarks.edit", args=[bookmark.id])
        base_url = reverse("linkding:bookmarks.read_later")

        # without query params
        return_url = urllib.parse.quote(base_url)
        url = f"{edit_url}?return_url={return_url}"

        response = self.client.get(base_url)
        self.assertEditLink(response, url)

        # with query
        url_params = "?q=foo"
        return_url = urllib.parse.quote(base_url + url_params)
        url = f"{edit_url}?return_url={return_url}"

        response = self.client.get(base_url + url_params)
        self.assertEditLink(response, url)

        # with query and sort and page
        url_params = "?q=foo&sort=title_asc&page=2"
        return_url = urllib.parse.quote(base_url + url_params)
        url = f"{edit_url}?return_url={return_url}"

        response = self.client.get(base_url + url_params)
        self.assertEditLink(response, url)

    def test_bulk_edit_respects_search_options(self):
        action_url = reverse("linkding:bookmarks.read_later.action")
        base_url = reverse("linkding:bookmarks.read_later")

        # without params
        url = f"{action_url}"

        response = self.client.get(base_url)
        self.assertBulkActionForm(response, url)

        # with query
        url_params = "?q=foo"
        url = f"{action_url}?q=foo"

        response = self.client.get(base_url + url_params)
        self.assertBulkActionForm(response, url)

        # with query and sort
        url_params = "?q=foo&sort=title_asc"
        url = f"{action_url}?q=foo&sort=title_asc"

        response = self.client.get(base_url + url_params)
        self.assertBulkActionForm(response, url)

    def test_allowed_bulk_actions(self):
        url = reverse("linkding:bookmarks.read_later")
        response = self.client.get(url)
        html = response.content.decode()

        self.assertInHTML(
            """
          <select name="bulk_action" class="form-select select-sm">
            <option value="bulk_archive">Archive</option>
            <option value="bulk_delete">Delete</option>
            <option value="bulk_tag">Add tags</option>
            <option value="bulk_untag">Remove tags</option>
            <option value="bulk_read">Mark as read</option>
            <option value="bulk_unread">Mark as unread</option>
            <option value="bulk_refresh">Refresh from website</option>
          </select>
        """,
            html,
        )

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_allowed_bulk_actions_with_html_snapshot_enabled(self):
        url = reverse("linkding:bookmarks.read_later")
        response = self.client.get(url)
        html = response.content.decode()

        self.assertInHTML(
            """
          <select name="bulk_action" class="form-select select-sm">
            <option value="bulk_archive">Archive</option>
            <option value="bulk_delete">Delete</option>
            <option value="bulk_tag">Add tags</option>
            <option value="bulk_untag">Remove tags</option>
            <option value="bulk_read">Mark as read</option>
            <option value="bulk_unread">Mark as unread</option>
            <option value="bulk_refresh">Refresh from website</option>
            <option value="bulk_snapshot">Create HTML snapshot</option>
          </select>
        """,
            html,
        )

    def test_allowed_bulk_actions_with_sharing_enabled(self):
        user_profile = self.user.profile
        user_profile.enable_sharing = True
        user_profile.save()

        url = reverse("linkding:bookmarks.read_later")
        response = self.client.get(url)
        html = response.content.decode()

        self.assertInHTML(
            """
          <select name="bulk_action" class="form-select select-sm">
            <option value="bulk_archive">Archive</option>
            <option value="bulk_delete">Delete</option>
            <option value="bulk_tag">Add tags</option>
            <option value="bulk_untag">Remove tags</option>
            <option value="bulk_read">Mark as read</option>
            <option value="bulk_unread">Mark as unread</option>
            <option value="bulk_share">Share</option>
            <option value="bulk_unshare">Unshare</option>
            <option value="bulk_refresh">Refresh from website</option>
          </select>
        """,
            html,
        )

    def test_search_form_omits_unread_filter(self):
        # The reading queue is inherently unread, so the unread filter is hidden;
        # sort (time triage) and the shared filter (status triage) remain.
        response = self.client.get(reverse("linkding:bookmarks.read_later"))
        html = response.content.decode()

        self.assertInHTML(
            '<label id="search-shared-label" class="form-label">Shared filter</label>',
            html,
        )
        self.assertInHTML(
            '<label id="search-unread-label" class="form-label">Unread filter</label>',
            html,
            count=0,
        )

    def test_apply_search_preferences(self):
        # no params
        response = self.client.post(reverse("linkding:bookmarks.read_later"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("linkding:bookmarks.read_later"))

        # some params
        response = self.client.post(
            reverse("linkding:bookmarks.read_later"),
            {
                "q": "foo",
                "sort": BookmarkSearch.SORT_TITLE_ASC,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("linkding:bookmarks.read_later") + "?q=foo&sort=title_asc",
        )

        # page is removed
        response = self.client.post(
            reverse("linkding:bookmarks.read_later"),
            {
                "q": "foo",
                "page": "2",
                "sort": BookmarkSearch.SORT_TITLE_ASC,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("linkding:bookmarks.read_later") + "?q=foo&sort=title_asc",
        )

    def test_save_search_preferences(self):
        user_profile = self.user.profile

        # with param
        self.client.post(
            reverse("linkding:bookmarks.read_later"),
            {
                "save": "",
                "sort": BookmarkSearch.SORT_TITLE_ASC,
            },
        )
        user_profile.refresh_from_db()
        self.assertEqual(
            user_profile.search_preferences,
            {
                "sort": BookmarkSearch.SORT_TITLE_ASC,
                "shared": BookmarkSearch.FILTER_SHARED_OFF,
                "unread": BookmarkSearch.FILTER_UNREAD_OFF,
            },
        )

    def test_url_encode_bookmark_actions_url(self):
        url = reverse("linkding:bookmarks.read_later") + "?q=%23foo"
        response = self.client.get(url)
        html = response.content.decode()
        soup = self.make_soup(html)
        actions_form = soup.select("form.bookmark-actions")[0]

        self.assertEqual(
            actions_form.attrs["action"],
            "/bookmarks/read-later/action?q=%23foo",
        )

    def test_encode_search_params(self):
        bookmark = self.setup_bookmark(description="alert('xss')", unread=True)

        url = reverse("linkding:bookmarks.read_later") + "?q=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")
        self.assertContains(response, bookmark.url)

        url = reverse("linkding:bookmarks.read_later") + "?sort=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")

        url = reverse("linkding:bookmarks.read_later") + "?page=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")

    def test_turbo_frame_details_modal_renders_details_modal_update(self):
        bookmark = self.setup_bookmark(unread=True)
        url = reverse("linkding:bookmarks.read_later") + f"?details={bookmark.id}"
        response = self.client.get(url, headers={"Turbo-Frame": "details-modal"})

        self.assertEqual(200, response.status_code)

        soup = self.make_soup(response.content.decode())
        self.assertIsNotNone(soup.select_one("turbo-frame#details-modal"))
        self.assertIsNone(soup.select_one("#bookmark-list-container"))
        self.assertIsNone(soup.select_one("#tag-cloud-container"))

    def test_does_not_include_rss_feed(self):
        response = self.client.get(reverse("linkding:bookmarks.read_later"))
        soup = self.make_soup(response.content.decode())

        feed = soup.select_one('head link[type="application/rss+xml"]')
        self.assertIsNone(feed)

    def test_renders_link_back_to_main_bookmark_list(self):
        # "Return to original list" affordance: the nav links back to the
        # active bookmark list.
        response = self.client.get(reverse("linkding:bookmarks.read_later"))
        soup = self.make_soup(response.content.decode())

        index_url = reverse("linkding:bookmarks.index")
        link = soup.select_one(f'a.menu-link[href="{index_url}"]')
        self.assertIsNotNone(link)

    def test_hide_bundles_when_enabled_in_profile(self):
        # visible by default
        response = self.client.get(reverse("linkding:bookmarks.read_later"))
        html = response.content.decode()

        self.assertInHTML('<h2 id="bundles-heading">Bundles</h2>', html)

        # hidden when disabled in profile
        user_profile = self.get_or_create_test_user().profile
        user_profile.hide_bundles = True
        user_profile.save()

        response = self.client.get(reverse("linkding:bookmarks.read_later"))
        html = response.content.decode()

        self.assertInHTML('<h2 id="bundles-heading">Bundles</h2>', html, count=0)
