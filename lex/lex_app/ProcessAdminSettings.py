from django.contrib import admin

from lex.lex_app.rest_api.process_admin_site import ProcessAdminSite

adminSite = admin.site
processAdminSite = ProcessAdminSite()
