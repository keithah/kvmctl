from kvmctl.sequences import SequencePlan


def test_screen_assertion_aborts_before_following_hid_action(tmp_path):
    from kvmctl.sequence_executor import SequenceExecutor
    from kvmctl.journal import Journal
    from test_sequence_executor import FakeClient, ready_session
    class ScreenClient(FakeClient):
        def snapshot_jpeg(self): self.calls.append(("snapshot",)); return b"frame"
        def ocr(self, frame): self.calls.append(("ocr",)); return "wrong screen"
    client = ScreenClient()
    ex = SequenceExecutor(client, ready_session(), Journal(tmp_path / "j.jsonl"), clock=lambda: 0.0, sleep=lambda _: None)
    plan = ex.plan({"target":"pve2", "actions":[
        {"type":"assert_screen", "contains":"expected"},
        {"type":"key", "value":"Enter"},
    ]})
    result = ex.execute(ex.authorize(plan, approved=True).token)
    assert not result.ok and result.error == "screen assertion failed"
    assert ("key_down", "Enter") not in client.calls
