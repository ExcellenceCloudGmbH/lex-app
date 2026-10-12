"""Migrations for rest_framework_api_key, served through MIGRATION_MODULES.

exc-djangorestframework-api-key 1.0.0, our fork, widened id and hashed_key to
150 characters in its model and ships only its 0001_initial, which makes them
100. Its keys need the width (a 145-character id), so 0002 widens them, under
the very name `lex init` used to generate into site-packages: databases that
applied that file see this one as applied. 0001_initial is the fork's, as it
ships. Drop this package once the fork ships the widening itself.
"""
