"""Reporting and PDF slip generation utilities for Electra election system."""
import io
from typing import List
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether
from elections.models import Election
from voters.models import ElectionVoter


def generate_voter_slips_pdf(election: Election) -> bytes:
    """Generate official A4 voter slips PDF for an election.
    
    Layout:
    - Standard A4 page (portrait: 595.27 x 841.89 pt).
    - 3-column by 11-row grid holding exactly 33 voter slips per page.
    - Card size matches previous standard (186 pt width x 65 pt height, 67.5 pt row grid).
    - Generous padding between voter details, photo square box, and card border.
    - Each slip includes:
      - Left section:
        - Election title in subtle grey with slip sequence number
        - Voter full name (bold)
        - Identifier (primary registry value) and Academic group
        - Allocated polling booth designation
      - Right section:
        - Empty square box with border for affixing the voter's passport size photo
      - Crisp outer border matching physical electoral roll slips
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=12,
        rightMargin=12,
        topMargin=10,
        bottomMargin=10,
    )

    # Styles
    election_style = ParagraphStyle(
        'SlipElectionGrey',
        fontName='Helvetica',
        fontSize=6.2,
        leading=7.2,
        textColor=colors.HexColor('#6B7280'),
    )
    name_style = ParagraphStyle(
        'SlipName',
        fontName='Helvetica-Bold',
        fontSize=8.2,
        leading=9.2,
        textColor=colors.HexColor('#111827'),
    )
    meta_style = ParagraphStyle(
        'SlipMeta',
        fontName='Helvetica',
        fontSize=6.6,
        leading=7.6,
        textColor=colors.HexColor('#374151'),
    )
    booth_style = ParagraphStyle(
        'SlipBooth',
        fontName='Helvetica-Bold',
        fontSize=7,
        leading=8,
        textColor=colors.HexColor('#111827'),
    )

    # Query all enrolled election voters with related voter and booth
    election_voters = (
        ElectionVoter.objects.filter(election=election)
        .select_related('voter', 'voter__academic_group', 'booth')
        .order_by('booth__booth_number', 'voter__name', 'voter__primary_registry_value')
    )

    slip_cells = []
    election_display_name = election.name[:24] + ("..." if len(election.name) > 24 else "")

    for idx, ev in enumerate(election_voters, start=1):
        voter = ev.voter
        voter_name = (voter.name[:22] + "...") if (voter and len(voter.name) > 22) else (voter.name if voter else "Voter")
        voter_id = voter.identifier if voter else "—"
        group_name = voter.academic_group.name if (voter and voter.academic_group) else "General"
        if len(group_name) > 14:
            group_name = group_name[:12] + "..."
        
        if ev.booth:
            booth_label = f"Booth {ev.booth.booth_number}"
            if ev.booth.name:
                short_bname = ev.booth.name[:12] + ("..." if len(ev.booth.name) > 12 else "")
                booth_label += f" ({short_bname})"
        else:
            booth_label = "Unallocated"

        # Left content: Election title, Name, Identifier, Group, and Booth details
        details_content = [
            Paragraph(f"<font color='#6B7280'>{election_display_name}</font> <font color='#9CA3AF'>&bull; #{idx}</font>", election_style),
            Spacer(1, 0.8),
            Paragraph(f"<b>{voter_name}</b>", name_style),
            Spacer(1, 0.8),
            Paragraph(f"ID: <b>{voter_id}</b> &nbsp;|&nbsp; {group_name}", meta_style),
            Spacer(1, 0.8),
            Paragraph(f"Booth: <b>{booth_label}</b>", booth_style),
        ]

        # Right content: Empty square box for passport size photo
        photo_box = Table([[""]], colWidths=[42], rowHeights=[48])
        photo_box.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#9CA3AF')),
            ('BACKGROUND', (0,0), (-1,-1), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))

        # Inner table pairing details and passport photo box with spacing
        inner_table = Table([[details_content, photo_box]], colWidths=[128, 44], rowHeights=[57])
        inner_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
            ('LEFTPADDING', (0,0), (0,0), 0),
            ('RIGHTPADDING', (0,0), (0,0), 4),
            ('LEFTPADDING', (1,0), (1,0), 0),
            ('RIGHTPADDING', (1,0), (1,0), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ]))

        # Slip table: exactly 186 pt wide by 65 pt high
        slip_table = Table([[inner_table]], colWidths=[186], rowHeights=[65])
        slip_table.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#9CA3AF')),
            ('BACKGROUND', (0,0), (-1,-1), colors.white),
            ('TOPPADDING', (0,0), (-1,-1), 3.5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
            ('LEFTPADDING', (0,0), (-1,-1), 5),
            ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ]))
        slip_cells.append(slip_table)

    if not slip_cells:
        # Placeholder slip if no voters
        content = [
            Paragraph(f"<font color='#6B7280'>{election_display_name}</font>", election_style),
            Spacer(1, 3),
            Paragraph("No voters enrolled in this election.", name_style),
        ]
        slip_table = Table([[content]], colWidths=[186], rowHeights=[65])
        slip_table.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#9CA3AF')),
            ('BACKGROUND', (0,0), (-1,-1), colors.white),
            ('PADDING', (0,0), (-1,-1), 4),
        ]))
        slip_cells.append(slip_table)

    # 3 columns x 11 rows = 33 slips per page
    story = []
    page_size = 33
    for page_start in range(0, len(slip_cells), page_size):
        page_slips = slip_cells[page_start:page_start + page_size]
        grid_data = []
        for i in range(0, len(page_slips), 3):
            row = []
            for j in range(3):
                if i + j < len(page_slips):
                    row.append(page_slips[i + j])
                else:
                    row.append("")
            grid_data.append(row)

        table = Table(grid_data, colWidths=[190, 190, 190], rowHeights=[67.5] * len(grid_data))
        table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0.5),
            ('TOPPADDING', (0,0), (-1,-1), 0.5),
            ('LEFTPADDING', (0,0), (-1,-1), 0.5),
            ('RIGHTPADDING', (0,0), (-1,-1), 0.5),
        ]))
        story.append(table)
        if page_start + page_size < len(slip_cells):
            story.append(PageBreak())

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def generate_election_results_pdf(election: Election, position_results: list, turnout: dict) -> bytes:
    """Generate official certified election results PDF report.
    
    Layout:
    - Standard A4 portrait (595.27 x 841.89 pt)
    - Pinned header with Election title, date, certified metadata
    - Turnout summary box
    - Position-by-position results tables
    - Highlighting winners, handling draw/tie across top candidates
    - Suppressing vote counts and percentages for single-candidate (unopposed) positions
    - Formal verification & signature seal at bottom
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = {
        'OrgHeader': ParagraphStyle(
            'ResultsOrgHeader',
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            textColor=colors.HexColor('#64748B'),
            alignment=1,  # Center
        ),
        'DocTitle': ParagraphStyle(
            'ResultsDocTitle',
            fontName='Helvetica-Bold',
            fontSize=18,
            leading=22,
            textColor=colors.HexColor('#0F172A'),
            alignment=1,  # Center
        ),
        'DocSubtitle': ParagraphStyle(
            'ResultsDocSubtitle',
            fontName='Helvetica',
            fontSize=10,
            leading=13,
            textColor=colors.HexColor('#475569'),
            alignment=1,  # Center
        ),
        'MetaText': ParagraphStyle(
            'ResultsMetaText',
            fontName='Helvetica',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#334155'),
            alignment=1,  # Center
        ),
        'PositionTitle': ParagraphStyle(
            'ResultsPosTitle',
            fontName='Helvetica-Bold',
            fontSize=13,
            leading=16,
            textColor=colors.HexColor('#0F172A'),
        ),
        'TableHeader': ParagraphStyle(
            'ResultsTableHeader',
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#475569'),
        ),
        'TableCell': ParagraphStyle(
            'ResultsTableCell',
            fontName='Helvetica',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#1E293B'),
        ),
        'TableCellBold': ParagraphStyle(
            'ResultsTableCellBold',
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#0F172A'),
        ),
        'WinnerCell': ParagraphStyle(
            'ResultsWinnerCell',
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#166534'),
        ),
        'TieWinnerCell': ParagraphStyle(
            'ResultsTieWinnerCell',
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#92400E'),
        ),
        'Footnote': ParagraphStyle(
            'ResultsFootnote',
            fontName='Helvetica',
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor('#94A3B8'),
            alignment=1,
        ),
    }

    story = []

    # 1. Header Banner
    story.append(Paragraph("ELECTRA — CONTROLLED POLLING-BOOTH ELECTION SYSTEM", styles['OrgHeader']))
    story.append(Spacer(1, 4))
    story.append(Paragraph(election.name, styles['DocTitle']))
    story.append(Spacer(1, 4))
    story.append(Paragraph("OFFICIAL CERTIFIED ELECTION RESULTS", styles['DocSubtitle']))
    story.append(Spacer(1, 8))

    # Completion Date & Summary line
    if election.closed_at:
        date_str = election.closed_at.strftime("%d %b %Y, %I:%M %p")
    elif election.ends_at:
        date_str = election.ends_at.strftime("%d %b %Y, %I:%M %p")
    else:
        date_str = "Concluded"

    ballots_cast = turnout.get("ballots_cast", 0)
    enrolled_count = turnout.get("enrolled_count", 0)
    turnout_pct = turnout.get("turnout_pct", 0.0)

    summary_html = (
        f"<b>Concluded:</b> {date_str} &nbsp;&bull;&nbsp; "
        f"<b>Total Votes Cast:</b> {ballots_cast:,} &nbsp;&bull;&nbsp; "
        f"<b>Registered Voters:</b> {enrolled_count:,} &nbsp;&bull;&nbsp; "
        f"<b>Turnout:</b> {turnout_pct}%"
    )
    story.append(Paragraph(summary_html, styles['MetaText']))
    story.append(Spacer(1, 14))

    # Divider bar
    divider = Table([[""]], colWidths=[523], rowHeights=[1])
    divider.setStyle(TableStyle([
        ('LINEABOVE', (0,0), (-1,-1), 0.75, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(divider)
    story.append(Spacer(1, 12))

    # 2. Position by Position Results
    for pres in position_results:
        pos = pres.get("position")
        candidates = pres.get("candidates", [])
        is_single = pres.get("is_single_candidate", len(candidates) == 1)
        total_votes = pres.get("total_votes", 0)

        pos_story = []
        pos_story.append(Paragraph(pos.name, styles['PositionTitle']))
        pos_story.append(Spacer(1, 6))

        if is_single and candidates:
            # Single candidate unopposed position: NO vote counts or turnout percentages!
            cand = candidates[0]
            cand_obj = cand.get("candidate")
            group_name = cand_obj.academic_group or cand_obj.symbol or "—"
            
            table_data = [
                [
                    Paragraph("Candidate Name", styles['TableHeader']),
                    Paragraph("Group", styles['TableHeader']),
                    Paragraph("Result", styles['TableHeader']),
                ],
                [
                    Paragraph(f"<b>{cand_obj.name}</b>", styles['TableCellBold']),
                    Paragraph(group_name, styles['TableCell']),
                    Paragraph("<b>WINNER (UNOPPOSED)</b>", styles['WinnerCell']),
                ]
            ]
            pos_table = Table(table_data, colWidths=[240, 160, 123], rowHeights=[20, 22])
            pos_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                ('BACKGROUND', (0,1), (-1,1), colors.HexColor('#F0FDF4')),
                ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('LINEBELOW', (0,0), (-1,0), 0.5, colors.HexColor('#E2E8F0')),
                ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ('LEFTPADDING', (0,0), (-1,-1), 8),
                ('RIGHTPADDING', (0,0), (-1,-1), 8),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ]))
            pos_story.append(pos_table)

        elif candidates:
            # Contested position with candidates
            table_data = [
                [
                    Paragraph("Candidate Name", styles['TableHeader']),
                    Paragraph("Group", styles['TableHeader']),
                    Paragraph("Votes", styles['TableHeader']),
                    Paragraph("Share", styles['TableHeader']),
                    Paragraph("Result", styles['TableHeader']),
                ]
            ]
            row_styles = []

            for row_idx, cand in enumerate(candidates, start=1):
                cand_obj = cand.get("candidate")
                votes = cand.get("votes", 0)
                pct = cand.get("percentage", 0.0)
                is_winner = cand.get("is_winner", False)
                is_tie = cand.get("is_tie", False)
                group_name = cand_obj.academic_group or cand_obj.symbol or "—"

                if is_winner:
                    if is_tie:
                        result_para = Paragraph("<b>WINNER (DRAW)</b>", styles['TieWinnerCell'])
                    else:
                        result_para = Paragraph("<b>WINNER</b>", styles['WinnerCell'])
                    row_bg = colors.HexColor('#F0FDF4')
                else:
                    result_para = Paragraph("Runner-up", styles['TableCell'])
                    row_bg = colors.HexColor('#FFFFFF') if row_idx % 2 == 1 else colors.HexColor('#F8FAFC')

                row_styles.append(('BACKGROUND', (0, row_idx), (-1, row_idx), row_bg))

                table_data.append([
                    Paragraph(f"<b>{cand_obj.name}</b>" if is_winner else cand_obj.name, styles['TableCellBold'] if is_winner else styles['TableCell']),
                    Paragraph(group_name, styles['TableCell']),
                    Paragraph(f"<b>{votes:,}</b>", styles['TableCellBold'] if is_winner else styles['TableCell']),
                    Paragraph(f"{pct}%", styles['TableCellBold'] if is_winner else styles['TableCell']),
                    result_para,
                ])

            pos_table = Table(table_data, colWidths=[180, 135, 65, 55, 88])
            table_style_rules = [
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('LINEBELOW', (0,0), (-1,0), 0.75, colors.HexColor('#CBD5E1')),
                ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ('LEFTPADDING', (0,0), (-1,-1), 8),
                ('RIGHTPADDING', (0,0), (-1,-1), 8),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ] + row_styles
            pos_table.setStyle(TableStyle(table_style_rules))
            pos_story.append(pos_table)

        pos_story.append(Spacer(1, 14))
        story.append(KeepTogether(pos_story))

    # 3. Certification Footer
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Official Electoral Record &bull; Certified and generated by Electra Election Management Platform &bull; Server-Authoritative Tally",
        styles['Footnote']
    ))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

