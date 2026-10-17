class HTMLReport:

    @classmethod
    def permission_view(cls, user_context) -> bool:
        """
        Override to decide who may open this report.

        Receives the caller's ``UserContext``. A report that refuses a caller
        answers 404, as if it did not exist. Default: every signed-in user.
        """
        return True

    def get_html(self, user):
        return ''
