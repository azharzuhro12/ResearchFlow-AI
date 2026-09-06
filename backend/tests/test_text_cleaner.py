"""Tests for text cleaning (pure local string processing)."""

from app.services.text_cleaner import clean_html, clean_text


def test_script_and_style_are_removed() -> None:
    html = """
    <html><head><style>body { color: red; }</style>
    <script>console.log("tracking");</script></head>
    <body><p>Readable paragraph.</p>
    <script>alert("nope")</script></body></html>
    """
    text = clean_html(html)

    assert "Readable paragraph." in text
    assert "color: red" not in text
    assert "console.log" not in text
    assert "alert" not in text


def test_navigation_noise_is_removed() -> None:
    html = """
    <html><body>
    <nav><a href="/">Home</a><a href="/about">About</a></nav>
    <header>Site Header</header>
    <footer>Copyright 2026</footer>
    <aside>Sidebar promo</aside>
    <main><p>The actual article content.</p></main>
    </body></html>
    """
    text = clean_html(html)

    assert "The actual article content." in text
    for noise in ("Home", "Site Header", "Copyright 2026", "Sidebar promo"):
        assert noise not in text


def test_html_comments_are_removed() -> None:
    html = "<p>Keep this.</p><!-- secret comment -->"
    assert "secret comment" not in clean_html(html)


def test_whitespace_is_normalized() -> None:
    html = "<p>Word   one</p><p>Word\t\ttwo</p>"

    text = clean_html(html)

    assert "Word one" in text
    assert "Word two" in text
    assert "\t" not in text


def test_non_breaking_spaces_are_collapsed() -> None:
    assert clean_html("<p>a  b</p>") == "a b"


def test_excessive_blank_lines_are_collapsed() -> None:
    text = "first\n\n\n\n\nsecond"
    assert clean_text(text) == "first\n\nsecond"


def test_empty_html_yields_empty_string() -> None:
    assert clean_html("<html><body></body></html>") == ""
    assert clean_html("") == ""


def test_clean_text_strips_and_ignores_blank_lines() -> None:
    assert clean_text("  hello  \n\n   \nworld \n") == "hello\n\nworld"
