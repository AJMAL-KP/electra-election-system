"""Reporting and PDF slip generation utilities for Electra election system."""
import io
from typing import List
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
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
