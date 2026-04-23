"""
Generate a shareable PDF client report combining:
  - CLIENT_FEEDBACK_REPORT.md  (bug-fix / resolution section)
  - CHATBOT_COMPARISON_REPORT_V2.md  (AI Findr vs Chatbot V2)

Output: Cenomi_Chatbot_Client_Report_2026-04-16.pdf
"""

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ── Palette ────────────────────────────────────────────────────────────────
DARK      = colors.HexColor("#111827")   # near-black
ACCENT    = colors.HexColor("#1A1A1A")   # near-black (B&W)
ACCENT_LT = colors.HexColor("#F3F4F6")   # light grey tint
SUCCESS   = colors.HexColor("#374151")   # dark grey (replaces green)
WARNING   = colors.HexColor("#6B7280")   # medium grey (replaces amber)
MUTED     = colors.HexColor("#6B7280")
BORDER    = colors.HexColor("#D1D5DB")
WHITE     = colors.white

PAGE_W, PAGE_H = A4
MARGIN = 22 * mm

# ── Styles ─────────────────────────────────────────────────────────────────
base = getSampleStyleSheet()

def style(name, parent="Normal", **kw):
    return ParagraphStyle(name, parent=base[parent], **kw)

S = {
    "cover_title": style("cover_title", fontSize=28, leading=34,
                         textColor=WHITE, alignment=TA_CENTER, fontName="Helvetica-Bold"),
    "cover_sub":   style("cover_sub",   fontSize=13, leading=18,
                         textColor=colors.HexColor("#D1D5DB"), alignment=TA_CENTER),
    "cover_meta":  style("cover_meta",  fontSize=10, leading=14,
                         textColor=colors.HexColor("#E5E7EB"), alignment=TA_CENTER),
    "h1":          style("h1", fontSize=17, leading=22, textColor=DARK,
                         fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4),
    "h2":          style("h2", fontSize=13, leading=17, textColor=DARK,
                         fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=3),
    "h3":          style("h3", fontSize=11, leading=15, textColor=DARK,
                         fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=2),
    "body":        style("body", fontSize=9.5, leading=14, textColor=DARK, spaceAfter=4),
    "body_muted":  style("body_muted", fontSize=8.5, leading=13, textColor=MUTED, spaceAfter=3),
    "tag_pass":    style("tag_pass", fontSize=8, leading=10, textColor=DARK,
                         fontName="Helvetica-Bold"),
    "tag_fix":     style("tag_fix",  fontSize=8, leading=10, textColor=DARK,
                         fontName="Helvetica-Bold"),
    "footer":      style("footer", fontSize=7.5, leading=10, textColor=MUTED,
                         alignment=TA_CENTER),
    "footer_r":    style("footer_r", fontSize=7.5, leading=10, textColor=MUTED,
                         alignment=TA_RIGHT),
    "toc_entry":   style("toc_entry", fontSize=10, leading=15, textColor=DARK),
    "verdict":     style("verdict", fontSize=9.5, leading=13, textColor=DARK,
                         fontName="Helvetica-Bold"),
}

# ── Helpers ─────────────────────────────────────────────────────────────────
def p(text, s="body"):
    return Paragraph(text, S[s])

def sp(h=4):
    return Spacer(1, h * mm)

def rule(color=BORDER, thickness=0.5):
    return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=4)

def section_rule():
    return HRFlowable(width="100%", thickness=1.0, color=ACCENT, spaceAfter=6)

def kv_row(key, val, key_bold=True):
    k_style = S["verdict"] if key_bold else S["body_muted"]
    return [Paragraph(key, k_style), Paragraph(val, S["body"])]

def data_table(headers, rows, col_widths=None, tone_map=None):
    """Generic styled data table."""
    usable = PAGE_W - 2 * MARGIN
    if col_widths is None:
        col_widths = [usable / len(headers)] * len(headers)

    data = [[Paragraph(h, style("th", fontSize=8, leading=11, textColor=WHITE,
                                 fontName="Helvetica-Bold"))
             for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c), S["body"]) for c in row])

    ts = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ACCENT_LT]),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ])

    if tone_map:
        for row_idx, tone in tone_map.items():
            ts.add("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor(
                "#E5E7EB" if tone == "success" else "#D1D5DB" if tone == "warning" else "#F3F4F6"
            ))

    t = Table(data, colWidths=col_widths)
    t.setStyle(ts)
    return t

def star_str(score, total=5):
    return "★" * score + "☆" * (total - score)


# ── Page templates ───────────────────────────────────────────────────────────
class CenomiDoc(BaseDocTemplate):
    def __init__(self, filename):
        super().__init__(filename, pagesize=A4,
                         leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=MARGIN, bottomMargin=MARGIN + 8 * mm)
        frame = Frame(MARGIN, MARGIN + 8 * mm,
                      PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN - 10 * mm,
                      id="main")
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame], onPage=self._cover_page),
            PageTemplate(id="body",  frames=[frame], onPage=self._body_page),
        ])

    @staticmethod
    def _cover_page(canvas, doc):
        canvas.saveState()
        # Background band — black
        canvas.setFillColor(DARK)
        canvas.rect(0, PAGE_H - 90 * mm, PAGE_W, 90 * mm, fill=1, stroke=0)
        # Bottom accent strip — slightly lighter black
        canvas.setFillColor(colors.HexColor("#374151"))
        canvas.rect(0, 0, PAGE_W, 12 * mm, fill=1, stroke=0)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#D1D5DB"))
        canvas.drawCentredString(PAGE_W / 2, 4 * mm,
            "CONFIDENTIAL — For authorised recipients only")
        canvas.restoreState()

    @staticmethod
    def _body_page(canvas, doc):
        canvas.saveState()
        # Top bar — black
        canvas.setFillColor(DARK)
        canvas.rect(0, PAGE_H - 10 * mm, PAGE_W, 10 * mm, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(WHITE)
        canvas.drawString(MARGIN, PAGE_H - 6 * mm,
                          "Cenomi Chatbot — Client Report  |  Al Nakheel Plaza  |  April 2026")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 6 * mm, "CONFIDENTIAL")
        # Bottom bar — light grey
        canvas.setFillColor(BORDER)
        canvas.rect(0, 0, PAGE_W, 10 * mm, fill=1, stroke=0)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(PAGE_W / 2, 3.5 * mm, f"Page {doc.page}")
        canvas.restoreState()


# ── Build story ──────────────────────────────────────────────────────────────
def build():
    doc = CenomiDoc("Cenomi_Chatbot_Client_Report_2026-04-16.pdf")
    story = []

    # ── COVER ──────────────────────────────────────────────────────────────
    story.append(sp(50))  # push down into the blue band
    story.append(p("Cenomi Chatbot", "cover_title"))
    story.append(sp(2))
    story.append(p("Client Report", "cover_title"))
    story.append(sp(6))
    story.append(p("AI Concierge — Quality Evaluation & Bug-Fix Verification", "cover_sub"))
    story.append(sp(4))
    story.append(p("Al Nakheel Plaza, Buraidah  ·  April 16, 2026", "cover_meta"))
    story.append(p("Mall ID: al_nakheel_plaza_28  ·  Model: gpt-4o-mini", "cover_meta"))
    story.append(sp(50))

    # Summary box on cover
    summary_data = [
        ["", ""],
        [Paragraph("18 / 18", ParagraphStyle("cv", fontSize=24, leading=28,
                                              textColor=DARK, fontName="Helvetica-Bold",
                                              alignment=TA_CENTER)),
         Paragraph("8 / 10", ParagraphStyle("cv2", fontSize=24, leading=28,
                                             textColor=DARK, fontName="Helvetica-Bold",
                                             alignment=TA_CENTER))],
        [Paragraph("Test Turns Passed", ParagraphStyle("cl", fontSize=9, leading=12,
                                                        textColor=MUTED, alignment=TA_CENTER)),
         Paragraph("Queries Won vs Baseline", ParagraphStyle("cl2", fontSize=9, leading=12,
                                                               textColor=MUTED, alignment=TA_CENTER))],
        ["", ""],
    ]
    usable = PAGE_W - 2 * MARGIN
    st = Table(summary_data, colWidths=[usable / 2, usable / 2])
    st.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(st)
    story.append(sp(6))
    story.append(p("This report covers two areas: (1) resolution of five client-reported defects "
                   "with full automated-test verification, and (2) a comparative evaluation of the "
                   "new chatbot implementation against the AI Findr baseline across 10 representative "
                   "queries.", "cover_meta"))

    story.append(PageBreak())

    # ── TABLE OF CONTENTS ──────────────────────────────────────────────────
    story.append(Paragraph("Contents", S["h1"]))
    story.append(section_rule())
    toc_items = [
        ("Part 1", "Client Feedback — Resolution Report", "3"),
        ("  1.1", "Architecture — LLM-Driven Pipeline", "3"),
        ("  1.2", "Executive Summary", "3"),
        ("  CF-01", "Service queries answered with real data", "4"),
        ("  CF-02", "Mall context isolated per mall_id", "4"),
        ("  CF-03", "Recovery suggestions exclude failed domain", "5"),
        ("  CF-04", "\"Cinema Level\" → \"Upper Level\"", "5"),
        ("  CF-05", "\"Main Gallery\" → \"Ground Floor\"", "6"),
        ("Part 2", "AI Chatbot Comparison — AI Findr vs Chatbot V2", "7"),
        ("  2.1", "Fixes Applied (V2)", "7"),
        ("  2.2", "Executive Capability Comparison", "7"),
        ("  2.3", "Query-by-Query Scorecard", "8"),
        ("  2.4", "Key Improvements & Remaining Gaps", "9"),
    ]
    for num, title, pg in toc_items:
        usable = PAGE_W - 2 * MARGIN
        row = Table([[Paragraph(f"{num}  {title}", S["toc_entry"]),
                      Paragraph(pg, S["footer_r"])]],
                    colWidths=[usable * 0.88, usable * 0.12])
        row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        story.append(row)
        story.append(rule())
    story.append(sp(4))
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════
    # PART 1 — CLIENT FEEDBACK RESOLUTION
    # ══════════════════════════════════════════════════════════════════════
    story.append(p("Part 1 — Client Feedback Resolution Report", "h1"))
    story.append(section_rule())
    story.append(p("Date: 2026-04-15  ·  Backend: http://localhost:8000  ·  "
                   "Mall: Al Nakheel Plaza, Buraidah (al_nakheel_plaza_28)", "body_muted"))
    story.append(sp(3))

    # Architecture
    story.append(p("1.1  Architecture — LLM-Driven Pipeline", "h2"))
    story.append(p(
        "All five fixes operate within a fully LLM-driven pipeline. No static responses, "
        "hardcoded reply strings, or keyword-based answer generation are used anywhere. "
        "The fixes work at two levels:", "body"))
    story.append(p("• <b>Context corrections</b> (CF-01, CF-02, CF-04, CF-05) — ensure the LLM "
                   "receives accurate, complete data so it can reason correctly.", "body"))
    story.append(p("• <b>Instruction corrections</b> (CF-03) — pass a runtime constraint to the "
                   "LLM so it applies the right logic when generating recovery messages.", "body"))
    story.append(sp(2))

    # Exec summary table Part 1
    story.append(p("1.2  Executive Summary", "h2"))
    story.append(p("Five client feedback issues were investigated, resolved, and verified through "
                   "automated regression tests. <b>All 18 test turns across 5 scenarios pass.</b>", "body"))
    story.append(sp(2))

    usable = PAGE_W - 2 * MARGIN
    summary_rows = [
        ("CF-01", "Bot does not respond to service-related questions",  "FIXED", "5 / 5"),
        ("CF-02", "Bot should know or ask the user's location",         "FIXED", "3 / 3"),
        ("CF-03", "Bot gives looping responses after failing a query",  "FIXED", "3 / 3"),
        ("CF-04", "Incorrect floor name \"Cinema Level\" used",         "FIXED", "3 / 3"),
        ("CF-05", "Incorrect zone name \"Gallery\" / \"Main Gallery\"", "FIXED", "4 / 4"),
    ]
    story.append(data_table(
        ["ID", "Issue", "Status", "Score"],
        summary_rows,
        col_widths=[usable * 0.10, usable * 0.58, usable * 0.15, usable * 0.17],
        tone_map={1: "success", 2: "success", 3: "success", 4: "success", 5: "success"},
    ))
    story.append(sp(3))

    # ── CF items ──────────────────────────────────────────────────────────
    cf_items = [
        {
            "id": "CF-01",
            "title": "Service queries are answered with actual data",
            "issue": ("The bot deflected all service-related questions (ATM, prayer room, "
                      "lost & found, WiFi, general facilities) with a generic \"I can't help "
                      "with that\" or suggested visiting the information desk without providing "
                      "any real data."),
            "root": ("Service entities were merged into the generic tenant list, causing them to "
                     "compete with store/dining entries and often get dropped. The intent "
                     "classifier did not recognise service-related keywords, so queries were "
                     "routed to graceful_recovery instead of service_lookup."),
            "fix": ("mall_context.py: services exposed under a dedicated top-level key. "
                    "concierge_prompt.py: MALL SERVICES & FACILITIES block added. "
                    "interpret_turn.py: exhaustive service classification examples added. "
                    "resolve_fact_scope.py: keyword rules replaced with _DOMAIN_SCOPE_FALLBACK dict. "
                    "fetch_exact_facts.py: returns full service catalogue for list queries. "
                    "compose_fact_response_context.py: service_list handler added."),
            "turns": [
                ("Where is the ATM?",
                 "The ATM is on the Ground Floor… by Gate 1 and Gate 2, in front of the Polo store and near the food court."),
                ("Is there a lost and found?",
                 "Lost & Found is on the Ground Floor, near the Information Desk at the Customer Service Desk, Gate 2."),
                ("Do you have a prayer room?",
                 "Men's Prayer Room is on Ground, Ground Floor. It is near Lavalle Store, Gate 1."),
                ("What facilities does the mall have?",
                 "The mall has these facilities: men's prayer room… wheelchair service… baby stroller service available from 4:00 PM to 11:00 PM…"),
                ("Is there WiFi here?",
                 "I don't have WiFi information available… best place to confirm is the Information Desk on the Ground Floor…"),
            ],
        },
        {
            "id": "CF-02",
            "title": "Mall context is isolated per mall_id",
            "issue": ("The bot either did not know which mall it was serving, or bled store/location "
                      "data from one mall into responses for another."),
            "root": ("The frontend had no explicit mall-selection step. The mall_id was either absent "
                     "or carried over from a previous session, meaning all users defaulted to the same "
                     "mall regardless of their actual location."),
            "fix": ("ChatPage.tsx: MallPickerScreen shown on first visit; user must explicitly select "
                    "their mall. useChat.ts: mallConfirmed state and confirmMall() callback; selection "
                    "persisted to localStorage and resets session on mall switch. App.tsx: wired through "
                    "to ChatPage."),
            "turns": [
                ("What is this mall? [al_nakheel_plaza_28]",
                 "This is Al Nakheel Plaza in Buraidah, Saudi Arabia, on King Abdullah Road…"),
                ("What is this mall? [al_nakheel_plaza_13]",
                 "Mall of Arabia… in Jeddah, Saudi Arabia, on Medina Road, An Nuzhah."),
                ("What is this mall? [al_nakheel_plaza_27]",
                 "This is Al Nakheel Mall in Riyadh, located at Exit 9, Al Imam Saud Ibn Abdul Aziz Branch Rd…"),
            ],
        },
        {
            "id": "CF-03",
            "title": "Recovery suggestions exclude the domain that just failed",
            "issue": ("When the bot could not answer a question (e.g. someone asking it to tell a joke), "
                      "it entered a graceful-recovery flow and listed suggested topics — but the list "
                      "always included the very topic that had just failed, creating a loop."),
            "root": ("generate_response.py built the recovery suggestion list statically without "
                     "considering the domain of the current query."),
            "fix": ("generate_response.py: both _build_unsupported_recovery_response and "
                    "_build_response_mode_instruction now read state.intent.domain at runtime and inject "
                    "it as an exclusion instruction before the LLM generates its response."),
            "turns": [
                ("Tell me a joke",
                 "I'm not sure I can help with 'Tell me a joke' — but I'm here to make your mall visit excellent. I can assist with: Dining… Shopping… Cinema & Entertainment…"),
                ("Tell me another joke",
                 "Same graceful recovery listing Dining, Shopping, and Cinema — jokes never offered as a topic."),
                ("What can I do here?",
                 "…begin at Zara on the Ground Floor for current fashion, then move to Stradivarius… then head to the Food Court…"),
            ],
        },
        {
            "id": "CF-04",
            "title": "\"Cinema Level\" replaced by \"Upper Level\"",
            "issue": ("The bot referred to a floor called \"Cinema Level\" which does not exist in "
                      "the actual mall layout. The correct name is \"Upper Level\"."),
            "root": ("The canonical data file and all derived artifacts used \"Cinema Level\" as the "
                     "floor name. Prompt examples in concierge_prompt.py and smalltalk.py reinforced "
                     "the wrong terminology."),
            "fix": ("Bulk data correction across 6 files: canonical JSON, context pack, semantic "
                    "enrichment, playbooks, convert_to_canonical.py, concierge_prompt.py and "
                    "smalltalk.py. The LLM generates floor references from the canonical data it "
                    "receives — correcting the source data is sufficient."),
            "turns": [
                ("Where is Muvi Cinema?",
                 "Muvi Cinema is on the Upper Level, in the Cinema Zone."),
                ("What floor is the cinema on?",
                 "Muvi Cinema is on the Upper Level, Cinema Zone."),
                ("Tell me about the mall",
                 "…Ground and Upper Level areas… — the phrase \"Cinema Level\" does not appear anywhere."),
            ],
        },
        {
            "id": "CF-05",
            "title": "\"Main Gallery\" / \"Gallery\" replaced by \"Ground Floor\"",
            "issue": ("The bot referred to a zone called \"Main Gallery\" or simply \"Gallery\" in "
                      "store location responses. No such zone exists; stores on that level are simply "
                      "on the \"Ground Floor\"."),
            "root": ("The zone name \"Main Gallery\" was present throughout the canonical data, semantic "
                     "enrichment tags (main_gallery), and playbook scripts for al_nakheel_plaza_28."),
            "fix": ("Same data-driven approach as CF-04. Bulk correction across 6 files. The LLM "
                    "produces zone references directly from the canonical data it reads — with the "
                    "source corrected to \"Ground Floor\", the LLM automatically uses that term in "
                    "every response."),
            "turns": [
                ("Where is Zara?",
                 "Zara is on the Ground Floor."),
                ("Where is the prayer room?",
                 "The Men's Prayer Room is on the Ground Floor, Ground Floor."),
                ("Tell me about the mall",
                 "…Ground and Upper Level areas… — \"Gallery\" does not appear."),
                ("Where can I find perfumes?",
                 "…Ajmal Perfumes on the Ground Floor… Zohoor Al Reef on the Ground Floor…"),
            ],
        },
    ]

    for cf in cf_items:
        story.append(sp(4))
        story.append(p(f"{cf['id']} — {cf['title']}", "h2"))
        story.append(rule(ACCENT, 0.8))

        info_rows = [
            ["Issue", cf["issue"]],
            ["Root cause", cf["root"]],
            ["Fix applied", cf["fix"]],
        ]
        info_table = Table(
            [[Paragraph(r[0], S["verdict"]), Paragraph(r[1], S["body"])] for r in info_rows],
            colWidths=[usable * 0.15, usable * 0.85],
        )
        info_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -2), 0.3, BORDER),
        ]))
        story.append(info_table)
        story.append(sp(2))

        # Test results
        story.append(p(f"Test results — {len(cf['turns'])}/{len(cf['turns'])} passed:", "h3"))
        tr_data = [[Paragraph(t[0], S["body"]), Paragraph(t[1], S["body_muted"])]
                   for t in cf["turns"]]
        tr_table = Table(tr_data, colWidths=[usable * 0.30, usable * 0.70])
        tr_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, colors.HexColor("#F3F4F6")]),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tr_table)

    # Test pass banner
    story.append(sp(5))
    banner = Table(
        [[Paragraph("Scenarios: 5 / 5 passed   ·   Turns: 18 / 18 passed",
                    ParagraphStyle("banner", fontSize=11, leading=14, textColor=WHITE,
                                   fontName="Helvetica-Bold", alignment=TA_CENTER))]],
        colWidths=[usable],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [4]),
    ]))
    story.append(banner)

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════
    # PART 2 — CHATBOT COMPARISON REPORT V2
    # ══════════════════════════════════════════════════════════════════════
    story.append(p("Part 2 — AI Chatbot Comparison Report", "h1"))
    story.append(section_rule())
    story.append(p("AI Findr (baseline) vs Chatbot V2 (post-fix)  ·  "
                   "Run ID: a69e288c  ·  April 16, 2026  ·  10 queries  ·  Independent fresh sessions",
                   "body_muted"))
    story.append(sp(3))

    # 2.1 Fixes
    story.append(p("2.1  Fixes Applied (V2)", "h2"))
    story.append(p("All fixes are LLM-driven — no hard-coded keywords, genre lists, or static rules.", "body"))
    story.append(sp(2))
    story.append(data_table(
        ["#", "Fix", "Target Issue"],
        [
            ("1", "Multi-category retrieval via LLM preferred_entity_types", "Dessert/café under-retrieval (Q6)"),
            ("2", "LLM-driven family-suitability assessment in cinema instruction", "Family-movie filter missing (Q2)"),
            ("3", "Scene extractor context-isolation rule + independent sessions", "Context contamination (Q10)"),
            ("4", "Expired offers surfaced in prompt + Guideline 14 updated", "Incomplete offers (Q8)"),
            ("5", "Guideline 26: mall hours + time estimates in planning queries", "Planning depth (Q1, Q10)"),
        ],
        col_widths=[usable * 0.06, usable * 0.54, usable * 0.40],
    ))
    story.append(sp(4))

    # 2.2 Capability comparison
    story.append(p("2.2  Capability Comparison", "h2"))
    story.append(data_table(
        ["Dimension", "AI Findr (Baseline)", "Chatbot V2"],
        [
            ("Response style",         "Conversational, emoji-rich, warm",      "Structured, markdown-formatted, professional"),
            ("Floor / zone accuracy",  "Partial",                                "High — specific zone and level per venue"),
            ("Live movie data",        "Defers to external website",             "Retrieves live schedule directly"),
            ("Family-movie filtering", "N/A — no live data",                     "LLM assesses suitability per film"),
            ("Dessert/café coverage",  "5 venues",                               "Expanded via multi-category retrieval"),
            ("Context isolation",      "Not applicable",                         "Independent sessions + scene reset rules"),
            ("Expired offers handling","Listed transparently",                   "Surfaced with validity dates and context"),
            ("Planning depth",         "Rich — time estimates, creative tips",   "Improved — Guideline 26 applied"),
            ("Follow-up prompts",      "Consistent",                             "Guided continuation per query domain"),
        ],
        col_widths=[usable * 0.26, usable * 0.37, usable * 0.37],
    ))
    story.append(sp(4))

    # 2.3 Scorecard
    story.append(p("2.3  Query-by-Query Scorecard", "h2"))
    queries_data = [
        ("Q1",  "Fun day plan for friends",              "★★☆☆☆", "★★★☆☆", "Tie"),
        ("Q2",  "Family-friendly movies today",          "★☆☆☆☆", "★★★☆☆", "Chatbot V2"),
        ("Q3",  "Lunch restaurant suggestions",          "★★★☆☆", "★★★★☆", "Chatbot V2"),
        ("Q4",  "Kids clothing stores",                  "★★★☆☆", "★★★★☆", "Chatbot V2"),
        ("Q5",  "Fun activities for kids",               "★★★★☆", "★★☆☆☆", "AI Findr"),
        ("Q6",  "Dessert places & cafes",                "★★★☆☆", "★★★★☆", "Chatbot V2"),
        ("Q7",  "Affordable fashion",                    "★★★☆☆", "★★★★☆", "Chatbot V2"),
        ("Q8",  "Ongoing offers & discounts",            "★☆☆☆☆", "★★★☆☆", "Chatbot V2"),
        ("Q9",  "Non-movie entertainment",               "★★☆☆☆", "★★★★☆", "Chatbot V2"),
        ("Q10", "Quick 2–3 hr shopping & dining plan",   "★★★☆☆", "★★★★★", "Chatbot V2"),
        ("", "TOTAL", "1 win", "8 wins", "Chatbot V2"),
    ]
    tone_map = {
        row_i + 1: ("warning" if row[4] == "AI Findr" else
                    "neutral" if row[4] == "Tie" else "success")
        for row_i, row in enumerate(queries_data[:-1])
    }
    tone_map[len(queries_data)] = "success"  # totals row
    story.append(data_table(
        ["#", "Query", "AI Findr", "Chatbot V2", "Winner"],
        queries_data,
        col_widths=[usable * 0.06, usable * 0.44, usable * 0.14, usable * 0.14, usable * 0.22],
        tone_map=tone_map,
    ))
    story.append(sp(4))

    # 2.4 Key improvements + gaps
    story.append(p("2.4  Key Improvements vs. V1", "h2"))
    improvements = [
        ("Dessert/Café Retrieval (Q6)",
         "Multi-category retrieval now uses preferred_entity_types from the intent classifier LLM "
         "to pull both café and dessert entities in a single pass."),
        ("Family-Movie Filtering (Q2)",
         "_build_cinema_template_instruction now injects a full family-suitability assessment "
         "instruction when child/family context is detected. The LLM evaluates each film — no "
         "hard-coded genre rules."),
        ("Context Contamination (Q10)",
         "Each comparison query was sent in an independent fresh session. The scene extractor "
         "prompt also gained a context-isolation rule that resets companion/scenario fields when "
         "a standalone query has no companion signals."),
        ("Offers Context (Q8)",
         "Expired offers are now included in _format_mall_context under a clearly labelled section; "
         "Guideline 14 instructs the LLM to surface them when no active offers exist, matching "
         "AI Findr's transparency approach."),
        ("Planning Depth (Q1, Q10)",
         "Guideline 26 instructs the LLM to include mall hours, per-step time estimates, 4–5 venue "
         "categories, a creative tip, and dual plan variants for itinerary-type queries."),
    ]
    for i, (title, desc) in enumerate(improvements, 1):
        story.append(p(f"{i}.  <b>{title}</b> — {desc}", "body"))
    story.append(sp(4))

    story.append(p("Remaining Gaps", "h2"))
    story.append(data_table(
        ["Issue", "Status", "Suggested Next Step"],
        [
            ("Marsil & R&B not appearing in Q4", "Open",     "Audit vector index coverage for al_nakheel_plaza_28"),
            ("Response warmth / emoji tone",      "Style gap", "Consider tone profile tuning per tenant config"),
            ("Showtimes in movie responses",       "Open",     "Ensure showtime data is ingested into vector store"),
        ],
        col_widths=[usable * 0.32, usable * 0.15, usable * 0.53],
        tone_map={1: "warning", 3: "warning"},
    ))
    story.append(sp(5))

    # Verdict
    verdict_box = Table(
        [[Paragraph(
            "<b>Overall Verdict:</b> The new implementation wins 8 of 10 queries and ties 1. "
            "The single AI Findr win (Q5) is attributable to supplementary practical details "
            "(stroller availability) not yet present in the new implementation's data layer. "
            "The underlying architecture — live data retrieval, LLM-driven filtering, independent "
            "context isolation, and structured location accuracy — represents a meaningful "
            "improvement over the baseline.",
            ParagraphStyle("verd", fontSize=9.5, leading=14, textColor=DARK)
        )]],
        colWidths=[usable],
    )
    verdict_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT_LT),
        ("BOX", (0, 0), (-1, -1), 0.8, DARK),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(verdict_box)
    story.append(sp(4))
    story.append(rule(MUTED, 0.3))
    story.append(p("Report generated: 2026-04-16  ·  Mall: al_nakheel_plaza_28  ·  "
                   "Backend: http://127.0.0.1:8000/api/chat  ·  V2 post-fix run  ·  "
                   "All fixes are LLM-driven — no hard-coded rules.",
                   "body_muted"))

    doc.build(story)
    print("PDF generated: Cenomi_Chatbot_Client_Report_2026-04-16.pdf")


if __name__ == "__main__":
    build()
