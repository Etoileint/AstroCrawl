"""AsyncCloseable Protocol tests."""

from __future__ import annotations

from astrobase import AsyncCloseable


class TestAsyncCloseableProtocol:
    def test_is_runtime_checkable(self):
        assert getattr(AsyncCloseable, "_is_runtime_protocol", False) is True

    def test_class_with_async_aclose_passes(self):
        class Impl:
            async def aclose(self) -> None:
                pass

        assert isinstance(Impl(), AsyncCloseable)

    def test_class_without_aclose_fails(self):
        class Impl:
            pass

        assert not isinstance(Impl(), AsyncCloseable)

    def test_class_with_only_other_methods_fails(self):
        class Impl:
            async def close(self) -> None:
                pass

            async def shutdown(self) -> None:
                pass

        assert not isinstance(Impl(), AsyncCloseable)

    def test_subclass_with_aclose_passes(self):
        class Base:
            async def aclose(self) -> None:
                pass

        class Sub(Base):
            pass

        assert isinstance(Sub(), AsyncCloseable)
