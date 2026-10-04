# Data migration to populate initial default VoterRegistry and link existing groups, voters, and elections.

from django.db import migrations


def populate_initial_registry(apps, schema_editor):
    VoterRegistry = apps.get_model('voters', 'VoterRegistry')
    AcademicGroup = apps.get_model('voters', 'AcademicGroup')
    Voter = apps.get_model('voters', 'Voter')
    Election = apps.get_model('elections', 'Election')

    # Get or create default registry
    default_registry, _ = VoterRegistry.objects.get_or_create(
        name="CET Students",
        defaults={
            "description": "College of Engineering Trivandrum voter registry",
            "primary_id_source": "Student ID",
            "name_source": "Name",
            "group_source": "Department",
            "subgroup_source": "Semester",
            "gender_source": "Gender",
        }
    )

    # Link any unlinked groups
    AcademicGroup.objects.filter(registry__isnull=True).update(registry=default_registry)

    # Link any unlinked voters
    Voter.objects.filter(registry__isnull=True).update(registry=default_registry)

    # Link any unlinked elections
    Election.objects.filter(voter_registry__isnull=True).update(voter_registry=default_registry)


def reverse_populate(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('voters', '0004_voterregistry_and_more'),
        ('elections', '0004_election_voter_registry'),
    ]

    operations = [
        migrations.RunPython(populate_initial_registry, reverse_populate),
    ]
