import pickle
import unittest

from lex.utilities.decorators.singleton import LexSingleton


@LexSingleton
class PickleableSingleton:
    init_calls = 0

    def __init__(self, value=None):
        type(self).init_calls += 1
        self.value = value


class LexSingletonTests(unittest.TestCase):
    def setUp(self):
        PickleableSingleton._singleton_instance = None
        PickleableSingleton._singleton_initialized = False
        PickleableSingleton.init_calls = 0

    def test_returns_same_instance_without_reinitializing(self):
        first = PickleableSingleton("first")
        second = PickleableSingleton("second")

        self.assertIs(first, second)
        self.assertEqual(first.value, "first")
        self.assertEqual(PickleableSingleton.init_calls, 1)

    def test_decorated_singleton_instances_are_pickleable(self):
        instance = PickleableSingleton("value")
        instance.metadata = {"source": "pickle-test"}

        restored = pickle.loads(pickle.dumps(instance))

        self.assertIs(restored, instance)
        self.assertEqual(restored.value, "value")
        self.assertEqual(restored.metadata, {"source": "pickle-test"})
