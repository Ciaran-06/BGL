import bgl


class TestPackage:
    def test_imports_with_a_version(self) -> None:
        assert isinstance(bgl.__version__, str)
        assert bgl.__version__
