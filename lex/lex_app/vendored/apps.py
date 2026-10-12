"""App configs that set what an installed package leaves to the project."""

from oauth2_authcodeflow.apps import Oauth2AuthcodeflowConfig


class OAuth2AuthCodeFlowConfig(Oauth2AuthcodeflowConfig):
    """oauth2_authcodeflow, with the default_auto_field its migrations were made with.

    The package sets none, so lex's DEFAULT_AUTO_FIELD (BigAutoField) would make
    its implicit id a bigint its migrations never created.
    """

    default_auto_field = "django.db.models.AutoField"
