"""URL patterns for elections application."""
from django.urls import path
from elections import views

app_name = "elections"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("create/", views.election_create_view, name="create"),
    path("<int:election_id>/edit/", views.election_edit_view, name="edit"),
    path("<int:election_id>/delete/", views.election_delete_view, name="delete"),
    path("<int:election_id>/start/", views.election_start_view, name="start"),
    path("<int:election_id>/close/", views.election_close_view, name="close"),
    path("<int:election_id>/publish-results/", views.election_publish_results_view, name="publish_results"),
    
    # Position endpoints
    path("<int:election_id>/positions/create/", views.position_create_view, name="position_create"),
    path("positions/<int:position_id>/delete/", views.position_delete_view, name="position_delete"),
    
    # Candidate endpoints
    path("positions/<int:position_id>/candidates/create/", views.candidate_create_view, name="candidate_create"),
    path("candidates/<int:candidate_id>/delete/", views.candidate_delete_view, name="candidate_delete"),
]
