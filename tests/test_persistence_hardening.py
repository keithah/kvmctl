import os, stat
from kvmctl.session_store import FileAuthorizationStore
from kvmctl.sequence_executor import SequenceAuthorization
from kvmctl.sequences import SequencePlan


def auth(token):
    p = SequencePlan.from_mapping({'target':'pve2','actions':[{'type':'release_all'}]})
    return SequenceAuthorization(p, 'pve2', 'sha256:x', 999, token=token, binding='b')


def test_store_preserves_multiple_capabilities_and_consumes_once(tmp_path):
    s = FileAuthorizationStore(str(tmp_path/'auth'))
    s.put(auth('one')); s.put(auth('two'))
    assert s.peek('one', binding='b').token == 'one'
    assert s.peek('two', binding='b').token == 'two'
    assert s.take('one', binding='b').token == 'one'
    assert s.take('one', binding='b') is None
    assert s.take('two', binding='b').token == 'two'


def test_store_rejects_unsafe_existing_files(tmp_path):
    path = tmp_path/'auth'; path.write_text('{}'); os.chmod(path, 0o644)
    s = FileAuthorizationStore(str(path))
    try: s.put(auth('one'))
    except PermissionError: pass
    else: raise AssertionError('unsafe file accepted')
