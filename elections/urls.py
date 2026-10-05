"""URL patterns for elections application."""
from django.urls import path
from elections import views

app_name = "elections"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("history/", views.election_history_view, name="history"),
    path("create/", views.election_create_view, name="create"),
    path("<int:election_id>/edit/", views.election_edit_view, name="edit"),
    path("<int:election_id>/delete/", views.election_delete_view, name="delete"),
    path("<int:election_id>/start/", views.election_start_view, name="start"),
    path("<int:election_id>/publish-results/", views.election_publish_results_view, name="publish_results"),

    # 4-Stage Election Setup Workflow
    path("setup/", views.election_setup_start_view, name="setup_start"),
    path("<int:election_id>/setup/resume/", views.election_setup_resume_view, name="setup_resume"),
    path("<int:election_id>/setup/save-draft/", views.election_setup_save_draft_view, name="setup_save_draft"),
    path("<int:election_id>/setup/discard/", views.election_setup_discard_view, name="setup_discard"),
    path("<int:election_id>/setup/voters/", views.election_setup_voters_view, name="setup_voters"),
    path("<int:election_id>/setup/voters/search/", views.election_setup_voters_search_view, name="setup_voters_search"),
    path("<int:election_id>/setup/details/", views.election_setup_details_view, name="setup_details"),
    path("<int:election_id>/setup/booths/", views.election_setup_booths_view, name="setup_booths"),
    path("<int:election_id>/setup/voter-slips/pdf/", views.election_voter_slips_pdf_view, name="setup_voter_slips_pdf"),
    path("<int:election_id>/setup/voter-slips/print/", views.election_voter_slips_print_view, name="setup_voter_slips_print"),
    path("<int:election_id>/setup/review/", views.election_setup_review_view, name="setup_review"),
    
    # Position endpoints
    path("<int:election_id>/positions/create/", views.position_create_view, name="position_create"),
    path("positions/<int:position_id>/delete/", views.position_delete_view, name="position_delete"),
    
    # Candidate endpoints
    path("positions/<int:position_id>/candidates/create/", views.candidate_create_view, name="candidate_create"),
    path("candidates/<int:candidate_id>/delete/", views.candidate_delete_view, name="candidate_delete"),

    # Election Voter Enrollment endpoints
    path("<int:election_id>/voters/", views.election_voters_view, name="voters"),
    path("<int:election_id>/voters/enroll/", views.election_enroll_voters_view, name="enroll_voters"),
    path("<int:election_id>/voters/<int:voter_id>/remove/", views.election_remove_voter_view, name="remove_voter"),
    path("<int:election_id>/voters/import/", views.election_import_voters_view, name="import_voters"),

    # Booth Management & Allocation endpoints
    path("<int:election_id>/booths/", views.election_booths_view, name="booths"),
    path("<int:election_id>/booths/create/", views.election_booth_create_view, name="booth_create"),
    path("<int:election_id>/booths/<int:booth_id>/delete/", views.election_booth_delete_view, name="booth_delete"),
    path("<int:election_id>/booths/<int:booth_id>/allocate/", views.election_booth_allocate_view, name="booth_allocate"),
    path("<int:election_id>/booths/auto-distribute/", views.election_booths_auto_distribute_view, name="booths_auto_distribute"),
    path("<int:election_id>/booths/<int:booth_id>/roster/", views.election_booth_roster_export_view, name="booth_roster"),

    # Station Credential Rotation endpoint
    path("devices/<int:device_id>/rotate-credentials/", views.device_rotate_credentials_view, name="device_rotate_credentials"),

    # Results endpoint
    path("<int:election_id>/results/", views.election_results_view, name="results"),
]

