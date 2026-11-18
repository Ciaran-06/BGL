import bgl


def test_package_imports_with_a_version() -> None:
    assert isinstance(bgl.__version__, str)
    assert bgl.__version__