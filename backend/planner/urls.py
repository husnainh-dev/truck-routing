from django.urls import path

from .views import PlanView, health

urlpatterns = [
    path("plan/", PlanView.as_view(), name="plan"),
    path("health/", health, name="health"),
]
