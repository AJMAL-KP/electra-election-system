"""URL patterns for voters application."""
from django.urls import path
from voters import views

app_name = "voters"

urlpatterns = [
    # Registries Home (displays all voter registries per 04_voter_registry_home.png)
    path("", views.registries_list_view, name="registries"),
    path("registry/", views.registry_view, name="registry"),
    path("create-registry/", views.registry_create_view, name="registry_create"),

    # Single Registry Detail View (Grouped accordion by Group/Sub Group)
    path("<int:registry_id>/", views.registry_view, name="registry_detail"),
    path("<int:registry_id>/delete/", views.registry_delete_view, name="registry_delete"),

    # Voter CRUD
    path("create/", views.voter_create_view, name="create"),
    path("<int:voter_id>/edit/", views.voter_edit_view, name="edit"),
    path("<int:voter_id>/delete/", views.voter_delete_view, name="delete"),
    path("bulk-delete/", views.voter_bulk_delete_view, name="bulk_delete"),
    path("import/", views.voter_import_view, name="import"),
    path("<int:registry_id>/import/", views.voter_import_view, name="registry_import"),
    path("import/parse/", views.voter_import_parse_view, name="import_parse"),
    path("enroll/<int:election_id>/", views.enroll_voters_view, name="enroll"),
    # Academic Group CRUD
    path("groups/create/", views.group_create_view, name="group_create"),
    path("groups/<int:group_id>/edit/", views.group_edit_view, name="group_edit"),
    path("groups/<int:group_id>/delete/", views.group_delete_view, name="group_delete"),
    path("groups/<int:group_id>/subgroups/create/", views.subgroup_create_view, name="subgroup_create"),
]
