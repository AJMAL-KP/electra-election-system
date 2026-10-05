"""Voter registry importers for CSV and Excel formats.

Owns:
- CSV and Excel parsing with flexible header mapping
- Automatic detection and user-specified mapping for Group (Level 1) and Sub Group (Level 2)
- Pre-import validation and dry-run preview
- Duplicate detection within the file and against existing registry records
- Automatic creation and resolution of two-level AcademicGroup hierarchy (Group -> Sub Group)
- Atomic import execution with voter assignment to leaf academic nodes
"""
import csv
import io
from typing import Any, Dict, List, Optional, Tuple
from django.core.exceptions import ValidationError
from django.db import transaction

from voters.models import AcademicGroup, AcademicGroupType, Voter, VoterRegistry


PRIMARY_ID_ALIASES = {
    'primary_registry_value', 'student_id', 'student id', 'studentid',
    'id', 'admission_no', 'admission no', 'admission_number', 'roll_no',
    'roll no', 'rollno', 'registration_no', 'reg_no', 'reg no', 'register_no',
    'register no', 'university_id', 'voter_id', 'voter id', 'unique_id',
    'unique id', 'identifier', 'usn', 'prn'
}

NAME_ALIASES = {'name', 'full_name', 'full name', 'student_name', 'student name', 'voter_name', 'voter name'}
GENDER_ALIASES = {'gender', 'sex'}
GROUP_ALIASES = {'group', 'academic_group', 'department', 'dept', 'class', 'program', 'programme', 'course', 'branch', 'faculty', 'discipline'}
SUBGROUP_ALIASES = {'subgroup', 'sub_group', 'sub-group', 'batch', 'section', 'sec', 'division', 'div', 'semester', 'sem', 'cohort', 'year'}


def normalize_header(header: str) -> str:
    """Normalize a header string for alias matching."""
    return header.strip().lower().replace('-', '_').replace(' ', '_')


def resolve_column_indices(
    headers: List[str],
    *,
    id_col: Optional[str] = None,
    name_col: Optional[str] = None,
    gender_col: Optional[str] = None,
    group_col: Optional[str] = None,
    subgroup_col: Optional[str] = None,
    registry: Optional[Any] = None,
) -> Dict[str, Optional[int]]:
    """Map standardized fields to column index in the uploaded spreadsheet/CSV.
    
    If explicit column names or a VoterRegistry schema is provided, they take precedence.
    Otherwise, standard aliases are matched automatically.
    """
    if registry:
        id_col = id_col or getattr(registry, "primary_id_source", None)
        name_col = name_col or getattr(registry, "name_source", None)
        group_col = group_col or getattr(registry, "group_source", None)
        subgroup_col = subgroup_col or getattr(registry, "subgroup_source", None)
        gender_col = gender_col or getattr(registry, "gender_source", None)

    mapping: Dict[str, Optional[int]] = {
        'primary_id': None,
        'name': None,
        'gender': None,
        'group': None,
        'subgroup': None,
    }

    # 1. Match explicit user-provided column names (exact or normalized)
    for idx, raw_h in enumerate(headers):
        clean_h = raw_h.strip()
        norm_h = normalize_header(clean_h)
        if id_col and (clean_h.lower() == id_col.strip().lower() or norm_h == normalize_header(id_col)):
            mapping['primary_id'] = idx
        if name_col and (clean_h.lower() == name_col.strip().lower() or norm_h == normalize_header(name_col)):
            mapping['name'] = idx
        if gender_col and (clean_h.lower() == gender_col.strip().lower() or norm_h == normalize_header(gender_col)):
            mapping['gender'] = idx
        if group_col and (clean_h.lower() == group_col.strip().lower() or norm_h == normalize_header(group_col)):
            mapping['group'] = idx
        if subgroup_col and (clean_h.lower() == subgroup_col.strip().lower() or norm_h == normalize_header(subgroup_col)):
            mapping['subgroup'] = idx

    # 2. For unassigned fields, match aliases
    for idx, raw_h in enumerate(headers):
        norm = normalize_header(str(raw_h))
        if mapping['primary_id'] is None and norm in PRIMARY_ID_ALIASES:
            mapping['primary_id'] = idx
        elif mapping['name'] is None and norm in NAME_ALIASES:
            mapping['name'] = idx
        elif mapping['gender'] is None and norm in GENDER_ALIASES:
            mapping['gender'] = idx
        elif mapping['group'] is None and norm in GROUP_ALIASES:
            mapping['group'] = idx
        elif mapping['subgroup'] is None and norm in SUBGROUP_ALIASES:
            mapping['subgroup'] = idx

    return mapping


class ImportResult:
    """Carries outcome and diagnostic reports for dry-run and commit imports."""

    def __init__(self):
        self.total_rows: int = 0
        self.valid_rows: int = 0
        self.new_voters: int = 0
        self.existing_voters: int = 0
        self.errors: List[Dict[str, Any]] = []
        self.preview: List[Dict[str, Any]] = []
        self.created_count: int = 0
        self.updated_count: int = 0
        self.detected_mapping: Dict[str, Optional[str]] = {}
        self.all_headers: List[str] = []
        self.validated_rows: List[Dict[str, Any]] = []
        self.category_summary: Dict[str, int] = {}
        self.unique_groups_count: int = 0
        self.unique_subgroups_count: int = 0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def parse_csv_content(file_obj) -> Tuple[List[str], List[List[str]]]:
    """Read CSV file object and return headers and data rows."""
    content = file_obj.read()
    if isinstance(content, bytes):
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('latin-1')
    else:
        text = str(content)

    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(field.strip() for field in r)]
    if not rows:
        raise ValidationError("The uploaded CSV file is empty.")

    headers = [h.strip() for h in rows[0]]
    data_rows = rows[1:]
    return headers, data_rows


def parse_excel_content(file_obj) -> Tuple[List[str], List[List[str]]]:
    """Read Excel (.xlsx) file object and return headers and data rows."""
    import openpyxl

    wb = openpyxl.load_workbook(file_obj, data_only=True)
    sheet = wb.active
    rows = []
    for r in sheet.iter_rows(values_only=True):
        if any(cell is not None and str(cell).strip() for cell in r):
            rows.append([str(c).strip() if c is not None else "" for c in r])

    if not rows:
        raise ValidationError("The uploaded Excel workbook contains no data.")

    headers = [str(h).strip() for h in rows[0]]
    data_rows = rows[1:]
    return headers, data_rows


def process_voter_import(
    *,
    headers: List[str],
    data_rows: List[List[str]],
    registry_id: Optional[int] = None,
    registry: Optional[VoterRegistry] = None,
    dry_run: bool = True,
    update_existing: bool = True,
    id_col: Optional[str] = None,
    name_col: Optional[str] = None,
    gender_col: Optional[str] = None,
    group_col: Optional[str] = None,
    subgroup_col: Optional[str] = None,
    default_group: Optional[str] = None,
    default_subgroup: Optional[str] = None,
    default_group_type: str = AcademicGroupType.GROUP,
    election_id: Optional[int] = None,
) -> ImportResult:
    """Validate and optionally commit imported voter rows into the central registry.
    
    Supports:
    - Auto-detection or explicit column mapping for:
      - Primary Identifier (Student ID, Roll No, etc.)
      - Name
      - Gender
      - Group (Level 1: Department, Class, etc.)
      - Sub Group (Level 2: Batch, Section, etc.)
    - Default fallback Group and Sub Group specified by the user.
    - Automatic creation of the two-level AcademicGroup hierarchy:
      - Level 1: GROUP (parent=None)
      - Level 2: SUBGROUP (parent=GROUP)
    - Voter assignment to the leaf node (Sub Group if present, otherwise Group).
    - Atomic transaction commitment.
    - Optional atomic enrollment into an active/draft election.
    """
    result = ImportResult()
    result.total_rows = len(data_rows)
    result.all_headers = headers

    explicit_registry = (registry is not None) or (registry_id is not None)
    if registry is None:
        if registry_id:
            try:
                registry = VoterRegistry.objects.get(id=registry_id)
            except VoterRegistry.DoesNotExist:
                raise ValidationError(f"Voter registry #{registry_id} does not exist.")
        else:
            registry = VoterRegistry.objects.first()
    elif registry_id is None:
        registry_id = registry.id

    if registry and registry.is_locked:
        raise ValidationError("Cannot import voters: this registry is linked to one or more elections.")

    col_map = resolve_column_indices(
        headers,
        id_col=id_col,
        name_col=name_col,
        gender_col=gender_col,
        group_col=group_col,
        subgroup_col=subgroup_col,
        registry=registry,
    )

    # Store detected mapping header names for UI display
    result.detected_mapping = {
        field: headers[idx] if idx is not None and idx < len(headers) else None
        for field, idx in col_map.items()
    }

    if col_map['primary_id'] is None:
        target_name = registry.primary_id_source if registry else "Student ID / Voter ID"
        raise ValidationError(
            f"The uploaded file is missing the required '{target_name}' column and cannot be used."
        )
    if col_map['name'] is None:
        target_name = registry.name_source if registry else "Name"
        raise ValidationError(
            f"The uploaded file is missing the required '{target_name}' column and cannot be used."
        )

    clean_default_group = (default_group or "").strip()
    clean_default_subgroup = (default_subgroup or "").strip() if (registry is None or registry.has_subgroups) else ""

    # Enforce registry schema: Sub Group is strictly required in the file if registry uses subgroups
    if explicit_registry and registry and registry.has_subgroups and col_map['subgroup'] is None and not clean_default_subgroup:
        target_sub = registry.subgroup_source or "Sub Group"
        raise ValidationError(
            f"The uploaded file is missing the required '{target_sub}' column and cannot be used."
        )

    # If registry does NOT use subgroups, omit and ignore any subgroup mapping
    if registry and not registry.has_subgroups:
        col_map['subgroup'] = None

    if explicit_registry and col_map['group'] is None and not clean_default_group:
        target_grp = registry.group_source if registry else "Group"
        raise ValidationError(
            f"The '{target_grp}' column is missing from the uploaded file. Please enter a default {target_grp} name."
        )
    seen_ids_in_file = set()
    validated_rows = []
    category_summary: Dict[str, int] = {}
    seen_groups = set()
    seen_subgroups = set()

    # Cache existing registry IDs for update vs new determination (scoped to this registry)
    voter_query = Voter.objects.all()
    if registry:
        voter_query = voter_query.filter(registry=registry)
    existing_registry_ids = set(
        voter_query.values_list('primary_registry_value', flat=True)
    )

    for row_num, row in enumerate(data_rows, start=2):
        # Extract fields
        primary_id_val = row[col_map['primary_id']].strip() if col_map['primary_id'] < len(row) else ""
        name_val = row[col_map['name']].strip() if col_map['name'] < len(row) else ""
        gender_val = row[col_map['gender']].strip() if col_map['gender'] is not None and col_map['gender'] < len(row) else ""
        
        # Group extraction: file column takes priority, falls back to default
        file_group_val = row[col_map['group']].strip() if col_map['group'] is not None and col_map['group'] < len(row) else ""
        file_subgroup_val = ""
        if registry is None or registry.has_subgroups:
            file_subgroup_val = row[col_map['subgroup']].strip() if col_map['subgroup'] is not None and col_map['subgroup'] < len(row) else ""

        final_group = file_group_val or clean_default_group
        final_subgroup = file_subgroup_val or clean_default_subgroup

        # Formatting label for display
        if final_group and final_subgroup:
            display_group = f"{final_group} › {final_subgroup}"
        elif final_group:
            display_group = final_group
        else:
            display_group = "Unassigned"

        # Row validation
        row_errors = []
        if not primary_id_val:
            row_errors.append(f"Row {row_num} is missing a Voter ID. Every row must have a unique Voter ID.")
        if not name_val:
            row_errors.append(f"Row {row_num} is missing a voter name. Every row must have a Name.")

        # Duplicate ID check within the uploaded file
        if primary_id_val in seen_ids_in_file:
            row_errors.append(f"The column selected for Voter ID has duplicate values ('{primary_id_val}' appears multiple times). Every voter must have a unique Voter ID.")
        else:
            seen_ids_in_file.add(primary_id_val)

        is_existing = primary_id_val in existing_registry_ids

        if row_errors:
            result.errors.append({
                'row_number': row_num,
                'primary_id': primary_id_val,
                'name': name_val,
                'errors': row_errors,
            })
        else:
            result.valid_rows += 1
            if is_existing:
                result.existing_voters += 1
            else:
                result.new_voters += 1

            if final_group:
                seen_groups.add(final_group.strip().lower())
            if final_subgroup:
                seen_subgroups.add((final_group.strip().lower(), final_subgroup.strip().lower()))
            category_summary[display_group] = category_summary.get(display_group, 0) + 1

            row_data = {
                'row_number': row_num,
                'primary_id': primary_id_val,
                'name': name_val,
                'gender': gender_val,
                'group_name': final_group,
                'subgroup_name': final_subgroup,
                'academic_group': display_group,
                'is_existing': is_existing,
            }
            validated_rows.append(row_data)
            if len(result.preview) < 20:
                result.preview.append(row_data)

    result.validated_rows = validated_rows
    result.category_summary = category_summary
    result.unique_groups_count = len(seen_groups)
    result.unique_subgroups_count = len(seen_subgroups)

    if dry_run or result.has_errors:
        return result

    # Execute atomic persistence
    with transaction.atomic():
        # Pre-resolve and auto-create the two-level AcademicGroup hierarchy
        group_cache: Dict[str, AcademicGroup] = {}
        subgroup_cache: Dict[Tuple[str, str], AcademicGroup] = {}

        for row in validated_rows:
            g_name = row['group_name']
            sg_name = row['subgroup_name']

            if g_name and g_name not in group_cache:
                # Find or create top-level group scoped to registry
                grp_qs = AcademicGroup.objects.filter(name__iexact=g_name, parent=None)
                if registry:
                    grp_qs = grp_qs.filter(registry=registry)
                grp = grp_qs.first()
                if not grp:
                    grp = AcademicGroup.objects.create(
                        registry=registry,
                        name=g_name,
                        type=AcademicGroupType.GROUP,
                        parent=None,
                    )
                group_cache[g_name] = grp

            if g_name and sg_name:
                parent_grp = group_cache[g_name]
                sub_key = (g_name, sg_name)
                if sub_key not in subgroup_cache:
                    sub_qs = AcademicGroup.objects.filter(name__iexact=sg_name, parent=parent_grp)
                    if registry:
                        sub_qs = sub_qs.filter(registry=registry)
                    sub = sub_qs.first()
                    if not sub:
                        sub = AcademicGroup.objects.create(
                            registry=registry,
                            name=sg_name,
                            type=AcademicGroupType.SUBGROUP,
                            parent=parent_grp,
                        )
                    subgroup_cache[sub_key] = sub

        imported_voters = []
        for row in validated_rows:
            g_name = row['group_name']
            sg_name = row['subgroup_name']

            target_group = None
            if g_name and sg_name:
                target_group = subgroup_cache.get((g_name, sg_name))
            elif g_name:
                target_group = group_cache.get(g_name)

            lookup = {'primary_registry_value': row['primary_id']}
            if registry:
                lookup['registry'] = registry

            voter, created = Voter.objects.update_or_create(
                defaults={
                    'name': row['name'],
                    'gender': row['gender'],
                    'academic_group': target_group,
                    'registry': registry,
                },
                **lookup,
            )
            imported_voters.append(voter)
            if created:
                result.created_count += 1
            else:
                result.updated_count += 1

        if election_id:
            from elections.models import Election
            from voters.models import ElectionVoter
            try:
                election = Election.objects.get(id=election_id)
                if not election.is_draft:
                    raise ValidationError("Configuration is frozen. Voters cannot be enrolled into an active or closed election.")
                for v in imported_voters:
                    ElectionVoter.objects.get_or_create(
                        election=election,
                        voter=v,
                        defaults={'has_voted': False}
                    )
            except Election.DoesNotExist:
                raise ValidationError(f"Election #{election_id} does not exist.")

    return result
