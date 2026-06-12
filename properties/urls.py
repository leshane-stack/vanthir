from django.urls import path

from properties import views

urlpatterns = [
    # Folio is the spine and the stable, unique key — canonical parcel URL.
    path("property/<str:folio>/", views.parcel_detail, name="parcel_detail"),
]
