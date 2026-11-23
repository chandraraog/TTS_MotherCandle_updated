from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PDF_FILE = Path(__file__).with_name("NIFTY_Cycle_Bot_Quick_Reference.pdf")


def create_pdf():
    doc = SimpleDocTemplate(
        str(PDF_FILE),
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Custom Color Palette
    PRIMARY = colors.HexColor("#1e293b")  # Dark Slate
    ACCENT = colors.HexColor("#2563eb")   # Royal Blue
    TEXT_DARK = colors.HexColor("#0f172a")

    # Custom Paragraph Styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=PRIMARY,
        spaceAfter=10,
    )

    h2_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=ACCENT,
        spaceBefore=12,
        spaceAfter=6,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        textColor=TEXT_DARK,
    )

    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.white,
    )

    story = []

    # Title Banner
    story.append(Paragraph("NIFTY 5m Cycle Bot — Quick Reference", title_style))
    story.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceBefore=0, spaceAfter=12))

    # 1. Core Project Files
    story.append(Paragraph("1. Core Project Files", h2_style))

    file_table_data = [
        [Paragraph("File", table_header_style), Paragraph("Purpose & Function", table_header_style)],
        [Paragraph("<b>config.json</b>", body_style), Paragraph("Central config file. Stores symbols, active timeframes ('5', '15'), and A-B-C origin timestamps.", body_style)],
        [Paragraph("<b>runner.py</b>", body_style), Paragraph("Master orchestrator. Reads config.json and launches cycle_bot.py background processes in parallel.", body_style)],
        [Paragraph("<b>cycle_bot.py</b>", body_style), Paragraph("Main bot engine. Fetches FYERS history, calculates 42/21/63 bar projections, evaluates closed candles, and sends Telegram alerts.", body_style)],
        [Paragraph("<b>last_alert_...txt</b>", body_style), Paragraph("Tracking file generated per stock/timeframe. Stores last alerted cycle time to prevent duplicate Telegram alerts.", body_style)],
        [Paragraph("<b>cycle_time_analyzer.py</b>", body_style), Paragraph("Pattern research script. Compares historical turn times against 42, 21, and 63 bar counts to detect cycle links.", body_style)],
        [Paragraph("<b>backtest_cycle.py</b>", body_style), Paragraph("Backtest engine. Projects Convergence and Mother Candle events forward to evaluate breakout performance.", body_style)],
    ]

    t_files = Table(file_table_data, colWidths=[130, 410])
    t_files.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t_files)
    story.append(Spacer(1, 10))

    # 2. Key Engine Rules
    story.append(Paragraph("2. Key Engine Rules", h2_style))
    rules_text = """
    • <b>Closed Candle Verification:</b> Evaluation takes place on <code>len(df) - 2</code> (the last closed candle), ensuring High/Low price boundaries are final before alerts trigger.<br/>
    • <b>Bar Stepping Calculation:</b> Future times use strict bar-count stepping (300s for 5m, 900s for 15m across 375-minute NSE sessions) to eliminate clock drift.<br/>
    • <b>Clean Console Output:</b> Terminal prints detailed summaries <i>only when a new cyclic line forms</i>. Intermediate checks display a lightweight heartbeat message.
    """
    story.append(Paragraph(rules_text, body_style))
    story.append(Spacer(1, 10))

    # 3. Telegram Alert Control (ALERT_MODE)
    story.append(Paragraph("3. Telegram Alert Control (ALERT_MODE)", h2_style))

    alert_table_data = [
        [Paragraph("ALERT_MODE Setting", table_header_style), Paragraph("Trigger Condition", table_header_style)],
        [Paragraph("<b>GENERAL</b>", body_style), Paragraph("Alerts on <b>EVERY</b> closed cyclic line (Normal, Convergence, or Mother Candle).", body_style)],
        [Paragraph("<b>SPECIAL</b>", body_style), Paragraph("Alerts <b>ONLY</b> on Convergence (2 lines) or Mother Candle (3 lines) events.", body_style)],
        [Paragraph("<b>MOTHER</b>", body_style), Paragraph("Alerts <b>ONLY</b> when a Mother Candle (3+ overlapping lines) occurs.", body_style)],
        [Paragraph("<b>CONVERGENCE</b>", body_style), Paragraph("Alerts <b>ONLY</b> when a Convergence (2 overlapping lines) occurs.", body_style)],
    ]

    t_alerts = Table(alert_table_data, colWidths=[130, 410])
    t_alerts.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), ACCENT),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t_alerts)
    story.append(Spacer(1, 10))

    # 4. Commands Reference
    story.append(Paragraph("4. Quick Commands Reference", h2_style))
    cmd_text = """
    <b>• Live Multi-Asset Setup (All Stocks/Timeframes):</b><br/>
    <code>python runner.py</code><br/><br/>
    <b>• Single Stock Execution (Standalone Mode):</b><br/>
    <code>python cycle_bot.py --symbol "NSE:NIFTY50-INDEX" --res "5" --loop</code><br/><br/>
    <b>• Historical Pattern Analyzer:</b><br/>
    <code>python cycle_time_analyzer.py</code><br/><br/>
    <b>• Backtest Engine:</b><br/>
    <code>python backtest_cycle.py</code>
    """
    story.append(Paragraph(cmd_text, body_style))

    # Build Document
    doc.build(story)
    print(f"PDF generated successfully: {PDF_FILE}")


if __name__ == "__main__":
    create_pdf()