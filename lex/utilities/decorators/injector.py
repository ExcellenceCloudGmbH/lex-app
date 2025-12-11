def inject(cls):
    """Decorator to inject the correct instance of a class."""

    def wrapper(instance_self, *args, **kwargs):
        # If the class is marked as a singleton, use the singleton instance
        if hasattr(cls, '_is_singleton') and cls._is_singleton:
            injected_instance = cls()
        else:
            # Otherwise, create a new instance
            injected_instance = cls(*args, **kwargs)

        # Find the name of the variable in the class to inject
        for name, value in instance_self.__class__.__dict__.items():
            if value == cls:
                setattr(instance_self, name, injected_instance)

    return wrapper


# Alias for backward compatibility
LexInjector = inject