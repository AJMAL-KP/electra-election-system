"""URL patterns for accounts application."""
from django.urls import path
from accounts import views

app_name = "accounts"

urlpatterns = [
    path('setup/', views.setup_view, name='setup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
]
