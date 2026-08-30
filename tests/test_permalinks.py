from __future__ import annotations

import datetime as dt
from pathlib import PurePosixPath

import pytest

from dolmen import permalinks


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Hello, World!", "hello-world"),
        ("  Spaced  Out  ", "spaced-out"),
        ("Ünïcodé Títle", "unicode-title"),
        ("already-a-slug", "already-a-slug"),
    ],
)
def test_slugify(value, expected):
    assert permalinks.slugify(value) == expected


def test_split_dated_filename():
    assert permalinks.split_dated_filename("2026-08-30-my-post") == (
        dt.date(2026, 8, 30),
        "my-post",
    )


def test_split_dated_filename_without_a_date():
    assert permalinks.split_dated_filename("about") == (None, "about")


def test_split_dated_filename_rejects_an_impossible_date():
    assert permalinks.split_dated_filename("2026-13-45-nope") == (None, "2026-13-45-nope")


def test_date_placeholders():
    url = permalinks.apply(
        "/:year/:month/:day/:title:output_ext",
        slug="my-post",
        date=dt.date(2026, 8, 5),
    )
    assert url == "/2026/08/05/my-post.html"


def test_named_style_pretty():
    url = permalinks.apply("pretty", slug="my-post", date=dt.date(2026, 8, 5))
    assert url == "/2026/08/05/my-post/"


def test_categories_are_slugified_into_the_path():
    url = permalinks.apply(
        "/:categories/:title/", slug="post", categories=["Robot Builds", "Pico"]
    )
    assert url == "/robot-builds/pico/post/"


def test_empty_categories_do_not_leave_a_double_slash():
    assert permalinks.apply("/:categories/:title/", slug="post") == "/post/"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("/blog/post.html", "blog/post.html"),
        ("/blog/post/", "blog/post/index.html"),
        ("/blog/post", "blog/post/index.html"),
        ("/", "index.html"),
    ],
)
def test_to_output_path(url, expected):
    assert permalinks.to_output_path(url) == PurePosixPath(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Jekyll replaces every non-alphanumeric run with a hyphen rather than
        # stripping it, which only shows up when punctuation sits *between*
        # characters. Stripping gave `v12-release` and `102554`.
        ("v1.2 Release", "v1-2-release"),
        ("10.25.54", "10-25-54"),
        ("Pi 5 & Friends", "pi-5-friends"),
        ("C++ Notes", "c-notes"),
        ("Screenshot 2026-08-28 at 10.25.54", "screenshot-2026-08-28-at-10-25-54"),
        ("--leading and trailing--", "leading-and-trailing"),
    ],
)
def test_slugify_replaces_punctuation_rather_than_stripping_it(value, expected):
    assert permalinks.slugify(value) == expected


def test_slugify_raw_mode_only_collapses_whitespace():
    assert permalinks.slugify("Hello, World!", "raw") == "Hello,-World!"
