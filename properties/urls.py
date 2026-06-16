from django.urls import path

from properties import views

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    # Folio is the spine and the stable, unique key — canonical parcel URL.
    path("property/<str:folio>/", views.parcel_detail, name="parcel_detail"),
]
