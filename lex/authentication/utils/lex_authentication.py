from lex.utilities.decorators.singleton import LexSingleton


@LexSingleton
class LexAuthentication:
    def load_settings(self, auth_module):
        # Dynamically load attributes from the settings module
        for attr in dir(auth_module):
            if not attr.startswith("__"):  # Avoid loading built-in attributes
                print(f"Loading attribute: {attr}")
                setattr(self, attr, getattr(auth_module, attr))