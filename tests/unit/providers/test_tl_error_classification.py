"""TL schema errors must not masquerade as credential failures."""

import json

from qwenpaw.exceptions import convert_model_exception, ModelExecutionException
from qwenpaw.providers.tl_errors import TLError
from qwenpaw.providers.tl_prompt_codec import parse_response
import pytest


@pytest.mark.parametrize("stage", ["response_parse", "tool_args_validation"])
def test_typed_validation_error_is_not_authentication(stage):
    error = TLError(stage, "call 0 has invalid keys", "invalid_shape")
    assert isinstance(
        convert_model_exception(error, "test"),
        ModelExecutionException,
    )


def test_exception_group_with_tl_prompt_error_is_model_execution():
    error = TLError(
        "prompt_encode",
        "TL provider supports text-only tool results",
        "unsupported_media",
    )
    grouped = ExceptionGroup("model request failed", [error])

    converted = convert_model_exception(grouped, "test")

    assert isinstance(converted, ModelExecutionException)


def test_call_shape_diagnostic_excludes_values_and_arbitrary_field_names():
    tool = {"type": "function", "function": {
        "name": "test", "parameters": {"type": "object"},
    }}
    text = json.dumps({"version": 1, "type": "tool_calls", "calls": [{
        "name": "test", "parameters": {"secret": "private-value"},
        "private-field-name": "private-value",
    }]})
    with pytest.raises(TLError) as caught:
        parse_response(text, tools=[tool])
    message = str(caught.value)
    assert "missing=arguments" in message
    assert "unexpected=parameters" in message
    assert "other_unexpected_count=1" in message
    assert "private" not in message
