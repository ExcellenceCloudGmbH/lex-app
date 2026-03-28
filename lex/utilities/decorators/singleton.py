def LexSingleton(cls):
    """
    Turn ``cls`` into a singleton without replacing it with a function.

    The previous implementation returned a factory function, which meant the
    module-level name no longer pointed at the class object. That breaks
    pickling for instances of decorated classes because pickle resolves
    ``module.ClassName`` back to the wrapper function instead of the class.
    """

    class SingletonWrapper(cls):
        _singleton_instance = None
        _singleton_initialized = False

        def __new__(inner_cls, *args, **kwargs):
            if inner_cls._singleton_instance is None:
                inner_cls._singleton_instance = super(SingletonWrapper, inner_cls).__new__(inner_cls)
            return inner_cls._singleton_instance

        def __init__(self, *args, **kwargs):
            if self._singleton_initialized:
                return
            super().__init__(*args, **kwargs)
            self._singleton_initialized = True

    SingletonWrapper.__name__ = cls.__name__
    SingletonWrapper.__qualname__ = cls.__qualname__
    SingletonWrapper.__module__ = cls.__module__
    SingletonWrapper.__doc__ = cls.__doc__

    return SingletonWrapper
