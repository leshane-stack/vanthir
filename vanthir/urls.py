from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.http import JsonResponse

from properties.sitemaps import ParcelSitemap


def healthz(_request):
    return JsonResponse({"status": "ok"})


sitemaps = {"parcels": ParcelSitemap}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="django.contrib.sitemaps.views.sitemap"),
    path("", include("properties.urls")),
]
