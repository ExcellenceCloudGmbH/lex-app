"""What lex supplies for installed packages, so `lex init` never writes into them.

Each package here ships models that its own migrations do not match under
lex's settings. Left alone, makemigrations would write a migration into the
package's folder in site-packages on every install, and the package's next
migration under the same number would conflict with it.
"""
