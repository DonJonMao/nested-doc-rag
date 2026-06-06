from nested_doc_rag.agent.trace import make_trace_event


def test_make_trace_event() -> None:
    event = make_trace_event("evt_1", "tool_call", "retrieved chunks")
    assert event.event_id == "evt_1"
    assert event.event_type == "tool_call"
    assert event.message == "retrieved chunks"
