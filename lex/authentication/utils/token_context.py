import contextvars

token_context = contextvars.ContextVar("token_context", default={'access_token': None})


class TokenContext:

    @staticmethod
    def get_access_token():
        return token_context.get("access_token")

    def __init__(self, access_token=None, request=None):
        if access_token:
            self.access_token = access_token
        if request:
            self.request = request
            if "access_token" in request:
                self.access_token = request["access_token"]

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def __enter__(self):
        if self.access_token:
            token_context.set({
                'access_token': self.access_token
            })