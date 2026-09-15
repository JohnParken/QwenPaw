from types import SimpleNamespace

from qwenpaw.providers.tl_utils import is_tl_formatter, is_tl_model


def test_is_tl_formatter_requires_boolean_true() -> None:
    assert is_tl_formatter(SimpleNamespace(_qwenpaw_tl_formatter=True))
    assert not is_tl_formatter(SimpleNamespace(_qwenpaw_tl_formatter=False))
    assert not is_tl_formatter(SimpleNamespace(_qwenpaw_tl_formatter=1))
    assert not is_tl_formatter(SimpleNamespace(_qwenpaw_tl_formatter="true"))


def test_is_tl_model_finds_wrapped_formatter() -> None:
    formatter = SimpleNamespace(_qwenpaw_tl_formatter=True)
    model = SimpleNamespace(formatter=formatter)
    assert is_tl_model(model)

    class ForwardingWrapper:
        @property
        def formatter(self):
            return model.formatter

    assert is_tl_model(ForwardingWrapper())
    assert not is_tl_model(None)

    inner = SimpleNamespace(formatter=formatter)
    wrapper = SimpleNamespace(formatter=SimpleNamespace(), _inner=inner)
    assert not is_tl_model(wrapper)
