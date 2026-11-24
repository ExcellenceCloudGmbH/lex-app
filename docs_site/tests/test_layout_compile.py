"""The docs app compiles, with the routes the site promises."""


def test_the_app_compiles_with_a_home_page(compiled):
    assert "index" in compiled.pages
