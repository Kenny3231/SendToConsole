from core.models import QuickEntry
from security.memory_provider import MemoryProvider
from security.provider import SecretProvider


def test_is_a_secret_provider_and_volatile():
    p = MemoryProvider()
    assert isinstance(p, SecretProvider) and p.volatile is True
    assert p.is_unlocked()


def test_save_load_lock_reset():
    p = MemoryProvider()
    p.save([QuickEntry("a", "1")])
    assert [e.label for e in p.load()] == ["a"]
    p.lock()
    assert p.load() == []
    p.save([QuickEntry("b")])
    p.reset()
    assert p.load() == []


def test_load_returns_a_copy_of_the_list():
    p = MemoryProvider()
    p.save([QuickEntry("a")])
    p.load().clear()
    assert len(p.load()) == 1
