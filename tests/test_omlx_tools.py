import json
from unittest.mock import patch

import pytest

from src.ai_engine.omlx_client import OmlxClient, OllamaClientError, _to_openai_messages

HISTORY = [
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "get_x", "arguments": {"a": 1}}},
        {"function": {"name": "get_y", "arguments": {}}},
    ]},
    {"role": "tool", "name": "get_y", "content": "Y"},
    {"role": "tool", "name": "get_x", "content": "X"},
]


def test_history_maps_to_openai_shape_with_matching_ids():
    out = _to_openai_messages(HISTORY)
    calls = out[1]["tool_calls"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"a": 1}
    ids = {c["function"]["name"]: c["id"] for c in calls}
    assert out[2]["tool_call_id"] == ids["get_y"] and out[3]["tool_call_id"] == ids["get_x"]
    assert "name" not in out[2]
    assert HISTORY[1]["tool_calls"][0]["function"]["arguments"] == {"a": 1}  # input untouched


def test_response_tool_call_arguments_parsed_to_dict():
    resp = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "t", "arguments": '{"k": 2}'}},
        {"id": "c2", "type": "function", "function": {"name": "t", "arguments": "not json"}},
    ]}}]}
    with patch.object(OmlxClient, "_make_request", return_value=resp):
        msg = OmlxClient().chat(messages=[], tools=[{"type": "function"}])["message"]
    assert msg["tool_calls"][0]["function"]["arguments"] == {"k": 2}
    assert msg["tool_calls"][1]["function"]["arguments"] == {}


def test_tools_sent_and_disabled_flag_blocks():
    client = OmlxClient()
    with patch.object(OmlxClient, "_make_request", return_value={"choices": [{"message": {}}]}) as m:
        client.chat(messages=[], tools=[{"type": "function"}])
    assert m.call_args.args[2]["tools"] == [{"type": "function"}]
    client._supports_tools = False
    with pytest.raises(OllamaClientError):
        client.chat(messages=[], tools=[{"type": "function"}])
    assert client.model_supports_tools("m") is False


def test_orphan_tool_message_not_given_minted_id():
    out = _to_openai_messages([{"role": "user", "content": "q"}, {"role": "tool", "name": "x", "content": "R"}])
    assert out[1]["role"] == "user" and "tool_call_id" not in out[1] and "R" in out[1]["content"]


def test_json_schema_response_format_shape():
    seen = {}

    def fake(self, method, endpoint, data=None):
        seen.update(data)
        return {"choices": [{"message": {"role": "assistant", "content": "{}"}}]}

    with patch.object(OmlxClient, "_make_request", fake):
        OmlxClient().chat(messages=[], format={"type": "object"})
    assert seen["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "response", "schema": {"type": "object"}}}


def test_stream_accumulates_tool_call_deltas():
    def sse(d):
        return ("data: " + json.dumps({"choices": [{"delta": d}]}) + "\n").encode()

    lines = [sse({"role": "assistant", "content": "hi"}),
             sse({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "t", "arguments": '{"k"'}}]}),
             sse({"tool_calls": [{"index": 0, "function": {"arguments": ': 3}'}}]}),
             b"data: [DONE]\n"]

    class Resp:
        def __enter__(self): return iter(lines)
        def __exit__(self, *a): return False

    with patch.object(OmlxClient, "_open", return_value=Resp()):
        chunks = list(OmlxClient().chat(messages=[], stream=True))
    assert chunks[0]["message"]["content"] == "hi"
    call = chunks[-1]["message"]["tool_calls"][0]
    assert call["id"] == "c1" and call["function"] == {"name": "t", "arguments": {"k": 3}}
