from django.contrib import admin

from lex.model_admin.sites.lex_admin_site import LexAdminSite

adminSite = admin.site
lexAdminSite = LexAdminSite()
