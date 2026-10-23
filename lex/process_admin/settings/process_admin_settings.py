from django.contrib import admin

from lex.process_admin.sites.process_admin_site import ProcessAdminSite

adminSite = admin.site
processAdminSite = ProcessAdminSite()
