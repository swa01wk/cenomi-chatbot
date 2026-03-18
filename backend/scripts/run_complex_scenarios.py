#!/usr/bin/env python3
"""
Run the complex multi-turn scenario tests from docs/test-queries-complex.md
and write a detailed results report to test-results/.

Each scenario is run as a single continuous session so context accumulates
across turns exactly as it would in a real conversation.
"""
import json
import os
import time
import uuid
import httpx
from datetime import datetime, timezone

BASE_URL = "http://127.0.0.1:8000/api/chat"
TIMEOUT  = 45   # complex turns can be slow

_RUN_ID = uuid.uuid4().hex[:8]

# ---------------------------------------------------------------------------
# Complex scenario definitions
# Each entry: (turn_id, query, expected_mode, expected_confidence, note)
# Turns sharing a session_key are sent in sequence within the same session.
# ---------------------------------------------------------------------------

SCENARIOS = [
    # ── S1: Anniversary Evening ──────────────────────────────────────────
    {
        "id": "S1",
        "label": "Anniversary Evening: Dietary Restrictions + Romantic Plan",
        "turns": [
            ("1.1",  "it's our wedding anniversary tonight",
             "context_acknowledgement", "high",
             "Must acknowledge anniversary warmly; offer 3 next-step directions"),
            ("1.2",  "we want something really special for dinner",
             "guided_recommendation", "high",
             "Must suggest romantic, upscale dining; NOT food courts"),
            ("1.3",  "my wife is vegetarian — does that change your suggestions?",
             "guided_recommendation", "high",
             "Must re-filter for vegetarian; NOT drop previous restaurants without explanation"),
            ("1.4",  "which of those have good vegetarian menus specifically?",
             "guided_recommendation", "high",
             "Must surface only venues with confirmed vegetarian options; NOT guess"),
            ("1.5",  "we'd also love some flowers or a small gift — is there anywhere here?",
             "guided_recommendation", "high",
             "Must surface gift/flower shops; NOT abandon dinner context"),
            ("1.6",  "can we do dinner and then a movie as a full night?",
             "hybrid_plan", "high",
             "Must produce structured 2-step plan; vegetarian constraint must carry through"),
            ("1.7",  "what's a good movie for couples?",
             "guided_recommendation", "high",
             "Must filter/highlight romantic films; NOT suggest action/horror as primary"),
            ("1.8",  "what time should we aim for dinner to make the 9pm show?",
             "hybrid_plan", "high",
             "Must give timing guidance; NOT refuse to advise on timing"),
            ("1.9",  "is 7pm reservation likely to be available on a weekend?",
             "guided_recommendation", "medium",
             "Must advise on peak-time likelihood; NOT hallucinate booking system"),
            ("1.10", "and something sweet after the movie — any dessert places?",
             "guided_recommendation", "high",
             "Must suggest dessert; note vegetarian-safe; NOT re-suggest dinner restaurant"),
            ("1.11", "something not too heavy",
             "guided_recommendation", "medium",
             "Must refine toward lighter desserts; NOT push heavy cake first"),
            ("1.12", "this has been really helpful — can you give me a full itinerary for tonight?",
             "hybrid_plan", "high",
             "Must produce time-sequenced itinerary with all constraints; NOT ignore dietary filter"),
        ],
    },

    # ── S2: Corporate Team Outing ─────────────────────────────────────────
    {
        "id": "S2",
        "label": "Corporate Team Outing (10 Colleagues, Mixed Preferences)",
        "turns": [
            ("2.1",  "i'm planning a team outing for about 10 people",
             "context_acknowledgement", "high",
             "Must acknowledge group context; offer 3 directions; NOT solo-visitor response"),
            ("2.2",  "we need a place to eat together — ideally a private or semi-private space",
             "guided_recommendation", "high",
             "Must suggest group/private dining; NOT small cafés or food courts"),
            ("2.3",  "what cuisines does the team get to choose from — what's available?",
             "guided_recommendation", "high",
             "Must list available cuisine types; formatted as clear choices"),
            ("2.4",  "the team has one person who only eats halal and one vegetarian — is that a problem?",
             "guided_recommendation", "high",
             "Must confirm halal standard in region and vegetarian options at specific venues"),
            ("2.5",  "great — which restaurant would you actually recommend for us?",
             "guided_recommendation", "high",
             "Must give ONE clear primary recommendation with reasoning; NOT a 5-option list"),
            ("2.6",  "after lunch, we want something fun we can all do together — any group activities?",
             "guided_recommendation", "high",
             "Must suggest group-compatible activities for 10 adults; NOT toddler areas"),
            ("2.7",  "how competitive are these? we want something with a bit of rivalry",
             "guided_recommendation", "medium",
             "Must refine toward competitive/game-based; NOT re-suggest cinema"),
            ("2.8",  "are there any meeting or briefing spaces in the mall if we need 10 mins to debrief?",
             "direct_factual", "medium",
             "Must answer honestly; suggest alternatives if no rooms; NOT invent business centre"),
            ("2.9",  "what's the best way to get 10 people to the mall — is there parking for multiple cars?",
             "direct_factual", "high",
             "Must give parking guidance; NOT ignore group-size context"),
            ("2.10", "how far in advance should we book the restaurant?",
             "guided_recommendation", "medium",
             "Must give practical booking advice; NOT say 'I can book it for you'"),
            ("2.11", "can you put together a rough schedule for a 3-hour team outing?",
             "hybrid_plan", "high",
             "Must produce time-blocked itinerary reflecting group size, halal+veg, competitive activity"),
            ("2.12", "what if we want to extend it to 4 hours?",
             "hybrid_plan", "medium",
             "Must suggest what to add/extend; NOT re-produce the same 3-hour plan"),
        ],
    },

    # ── S3: International Tourist ─────────────────────────────────────────
    {
        "id": "S3",
        "label": "International Tourist: Culture, Local Food & Souvenirs",
        "turns": [
            ("3.1",  "i'm a tourist visiting Saudi Arabia for the first time — what should I know about this mall?",
             "context_acknowledgement", "high",
             "Must give cultural orientation: structure, prayer times, highlights; NOT dump store list"),
            ("3.2",  "where can I try real Saudi or Arabic food?",
             "guided_recommendation", "high",
             "Must surface Saudi/Arabic/Middle Eastern cuisine; NOT fast-food chains as primary"),
            ("3.3",  "what dishes would you recommend for someone who's never had Arabic food?",
             "guided_recommendation", "high",
             "Must give 3-5 beginner-friendly dish recommendations; NOT just 'try everything'"),
            ("3.4",  "i need to pray — where are the prayer rooms and when are the prayer times?",
             "direct_factual", "high",
             "Must give prayer room location; NOT guess prayer times"),
            ("3.5",  "does the mall pause business during prayer time?",
             "direct_factual", "high",
             "Must give accurate mall operations info; NOT guess or contradict"),
            ("3.6",  "i want to buy souvenirs — traditional Saudi items, not generic tourist stuff",
             "guided_recommendation", "high",
             "Must surface shops with authentic local goods; NOT suggest electronics/international fashion"),
            ("3.7",  "what's a good price range for oud (the perfume)?",
             "guided_recommendation", "medium",
             "Must give realistic range or direct to staff; NOT hallucinate prices"),
            ("3.8",  "is Arabic coffee sold anywhere here? i want to take some home",
             "direct_factual", "high",
             "Must answer yes/no definitively with location; NOT hedge"),
            ("3.9",  "where should I go to take a nice photo or two — any Instagram-worthy spots in the mall?",
             "guided_recommendation", "medium",
             "Must suggest visually distinctive areas; NOT say 'I don't know'"),
            ("3.10", "my phone battery is dying — is there anywhere to charge it?",
             "direct_factual", "high",
             "Must answer with charging station or alternatives; NOT suggest buying charger first"),
            ("3.11", "i only have about 2 hours left here — what should I prioritize?",
             "hybrid_plan", "high",
             "Must build prioritised 2-hour plan; NOT suggest fashion shopping over cultural experiences"),
            ("3.12", "can you recommend any good halal restaurants nearby the mall for tonight?",
             "graceful_recovery", "low",
             "Must clarify can only advise inside mall; NOT make up external recommendations"),
            ("3.13", "shukran! one last thing — do you have any Cenomi loyalty card offers I can use as a tourist?",
             "direct_factual", "medium",
             "Must give honest loyalty scheme info; NOT fabricate offers"),
        ],
    },

    # ── S4: Child's Birthday Party ────────────────────────────────────────
    {
        "id": "S4",
        "label": "Child's Birthday Party Planning",
        "turns": [
            ("4.1",  "i'm planning my daughter's 7th birthday at the mall",
             "context_acknowledgement", "high",
             "Must acknowledge birthday planning; offer 3 directions; NOT suggest adult entertainment"),
            ("4.2",  "what activities would a 7-year-old girl enjoy here?",
             "guided_recommendation", "high",
             "Must surface age-appropriate kids activities; NOT cinema as only option"),
            ("4.3",  "she loves dancing and arts and crafts — anything like that?",
             "guided_recommendation", "high",
             "Must refine to creative/performing arts; if no exact match say so honestly"),
            ("4.4",  "where can we do the birthday lunch — she wants pizza",
             "guided_recommendation", "high",
             "Must suggest pizza restaurants, family-friendly with group seating; NOT fine dining"),
            ("4.5",  "we'll have about 12 kids and 6 parents — is that size okay?",
             "guided_recommendation", "high",
             "Must address group size 18; recommend venues that accommodate; flag need to book"),
            ("4.6",  "can i get a birthday cake from somewhere here?",
             "direct_factual", "high",
             "Must answer yes/no definitively with location(s); NOT 'there might be'"),
            ("4.7",  "do any of them do custom cakes with 24 hours notice?",
             "direct_factual", "medium",
             "Must give honest answer; direct to store if unknown; NOT confirm without data"),
            ("4.8",  "i need to get her a birthday present too — she loves unicorns and art supplies",
             "guided_recommendation", "high",
             "Must suggest toy/gift stores and art supply shops; NOT fashion/electronics as primary"),
            ("4.9",  "budget around 150 SAR for the gift",
             "guided_recommendation", "medium",
             "Must acknowledge budget and refine; NOT suggest premium boutiques"),
            ("4.10", "is there face painting or any party entertainment here?",
             "direct_factual", "medium",
             "Must answer based on known data; if not available say so; NOT speculate"),
            ("4.11", "what's the best order to do everything — activities first or lunch first?",
             "hybrid_plan", "high",
             "Must produce logical birthday flow: activities → lunch → cake → gift"),
            ("4.12", "my daughter has a nut allergy — i should check the pizza place, right?",
             "guided_recommendation", "high",
             "Must strongly affirm need to check; NOT say 'pizza is usually fine'; NOT downplay allergy"),
            ("4.13", "thank you — can you give me a birthday checklist?",
             "hybrid_plan", "high",
             "Must produce structured checklist specific to conversation context"),
        ],
    },

    # ── S5: Multi-Generational Family ────────────────────────────────────
    {
        "id": "S5",
        "label": "Multi-Generational Family: Grandparents + Parents + Toddlers",
        "turns": [
            ("5.1",  "we're a big family — grandparents, parents, and four kids between 2 and 10",
             "context_acknowledgement", "high",
             "Must acknowledge multi-generational group; offer 3 directions"),
            ("5.2",  "one of the grandparents uses a walking frame — is the mall accessible?",
             "direct_factual", "high",
             "Must give clear accessibility info: lifts, ramps, seating; NOT vague answer"),
            ("5.3",  "is there a stroller we can borrow for the 2-year-old?",
             "direct_factual", "high",
             "Must answer yes/no definitively; give pickup location if available"),
            ("5.4",  "where can the grandparents sit and relax while we take the kids somewhere?",
             "guided_recommendation", "high",
             "Must suggest comfortable seating/cafés/rest zones; NOT active entertainment"),
            ("5.5",  "what activities work for kids ranging from 2 to 10?",
             "guided_recommendation", "high",
             "Must surface activities with wide age range; NOT exclude 2-year-old"),
            ("5.6",  "can we all do something together — grandparents included?",
             "guided_recommendation", "medium",
             "Must suggest low-intensity inclusive experiences; NOT escape rooms or physically demanding"),
            ("5.7",  "where should we eat with 8 people including young kids and elderly?",
             "guided_recommendation", "high",
             "Must prioritise: accessible, child-friendly, quiet, high chairs, group table"),
            ("5.8",  "we want somewhere not too loud — the grandparents find it hard to hear in noisy places",
             "guided_recommendation", "high",
             "Must refine toward quieter dining; NOT re-suggest loud open-plan food courts"),
            ("5.9",  "the kids are starting to get tired — how do we wind down the visit?",
             "hybrid_plan", "high",
             "Must suggest calming end-of-visit routine; NOT suggest energising activities"),
            ("5.10", "is there a family toilet or a changing room for the toddler?",
             "direct_factual", "high",
             "Must give floor/zone for baby changing; no hedging"),
            ("5.11", "the grandparents want to buy the kids a small gift each before we leave",
             "guided_recommendation", "high",
             "Must suggest accessible gift/toy shops; NOT stores requiring stairs"),
            ("5.12", "can you give us a family-friendly plan for a 4-hour visit?",
             "hybrid_plan", "high",
             "Must account for: grandparent mobility, toddler nap risk, kids activities, rest period"),
            ("5.13", "one of the kids has a peanut allergy and is lactose intolerant — what should we watch for at the restaurant?",
             "guided_recommendation", "high",
             "Must strongly recommend checking with staff; NOT confirm specific items are safe"),
        ],
    },

    # ── S6: Back-to-School Power Shopper ─────────────────────────────────
    {
        "id": "S6",
        "label": "Back-to-School Power Shopper (Budget + Time Pressured)",
        "turns": [
            ("6.1",  "school is starting next week and i need to shop for three kids — ages 6, 11, and 15",
             "context_acknowledgement", "high",
             "Must acknowledge back-to-school context; offer structured approach"),
            ("6.2",  "i need school bags, stationery, and shoes for all three — different sizes obviously",
             "guided_recommendation", "high",
             "Must surface stores covering all 3 categories; note multi-category stores"),
            ("6.3",  "what's the most efficient order to visit the stores — i want to minimize walking",
             "guided_recommendation", "high",
             "Must give routing suggestion by floor/zone proximity; NOT alphabetically sorted list"),
            ("6.4",  "my budget is 500 SAR for everything — is that realistic?",
             "guided_recommendation", "medium",
             "Must give honest guidance with per-category breakdown; NOT say 'that's plenty'"),
            ("6.5",  "which stores give the best value for money?",
             "guided_recommendation", "medium",
             "Must surface value-for-money stores; NOT recommend luxury brands"),
            ("6.6",  "the 15-year-old is very particular about style — any trendy options for teens?",
             "guided_recommendation", "high",
             "Must surface teen-oriented fashion; maintain budget constraint; NOT kids brands"),
            ("6.7",  "she specifically wants Nike or Adidas shoes",
             "direct_factual", "high",
             "Must confirm yes/no whether brands are present and give location; NOT guess"),
            ("6.8",  "for the 6-year-old I need something durable — he destroys shoes in a month",
             "guided_recommendation", "high",
             "Must suggest durable kids footwear; NOT fashion-over-function options"),
            ("6.9",  "is there a stationery store with a good back-to-school section?",
             "direct_factual", "high",
             "Must answer yes/no with location; NOT generic redirect"),
            ("6.10", "do they do pre-packed school supply bundles or only individual items?",
             "direct_factual", "medium",
             "Must answer honestly; direct to store if unknown; NOT guess"),
            ("6.11", "i also need to grab a quick lunch mid-shop — something i can eat on the go",
             "guided_recommendation", "high",
             "Must suggest grab-and-go; NOT sit-down restaurants; position within shopping route"),
            ("6.12", "i have 2.5 hours — can you map out a shopping plan?",
             "hybrid_plan", "high",
             "Must produce time-efficient floor-by-floor plan with lunch break and buffer"),
            ("6.13", "what if i can't find everything — what's the priority order?",
             "guided_recommendation", "high",
             "Must give reasoned priority order; NOT just a list"),
        ],
    },

    # ── S7: Luxury / VIP Experience Seeker ───────────────────────────────
    {
        "id": "S7",
        "label": "Luxury / VIP Experience Seeker",
        "turns": [
            ("7.1",  "i'm looking for a premium experience today — money is not a concern",
             "context_acknowledgement", "high",
             "Must acknowledge luxury framing; adopt concierge tone; NOT immediately list brand names"),
            ("7.2",  "start with fashion — i want to refresh my wardrobe, both casual and formal",
             "guided_recommendation", "high",
             "Must suggest premium fashion for both categories; NOT high-street or budget brands"),
            ("7.3",  "any of those offer personal shopping assistance or styling?",
             "direct_factual", "medium",
             "Must answer honestly; if available name which stores; NOT fabricate a service"),
            ("7.4",  "i also want to find something truly unique — a gift that's not off the shelf",
             "guided_recommendation", "high",
             "Must suggest bespoke/artisanal/limited-availability gifts; NOT generic gift shops"),
            ("7.5",  "ideally something locally crafted or Saudi-inspired",
             "guided_recommendation", "high",
             "Must narrow to locally sourced/culturally authentic; NOT imported luxury as 'local'"),
            ("7.6",  "where should I have lunch — i want exceptional food, not just fine dining for its own sake",
             "guided_recommendation", "high",
             "Must recommend based on quality and experience; NOT generic 'upscale restaurant' answer"),
            ("7.7",  "can you tell me more about the menu or signature dishes there?",
             "guided_recommendation", "medium",
             "Must give honest specific info if available; NOT fabricate menu items"),
            ("7.8",  "after lunch i want a beauty or grooming treatment — any high-end options?",
             "guided_recommendation", "high",
             "Must surface premium beauty/grooming/spa; NOT basic hair salon without context"),
            ("7.9",  "i'd like to end the day with a tasting or something experiential — not just shopping",
             "guided_recommendation", "medium",
             "Must suggest experiential: coffee tasting, oud experience, etc; NOT cinema as primary"),
            ("7.10", "is there anywhere that does a proper Arabic coffee or oud ceremony?",
             "direct_factual", "medium",
             "Must answer honestly; give location if available; NOT make up"),
            ("7.11", "what's the most exclusive item or experience I probably couldn't find elsewhere in the city?",
             "best_effort_shortlist", "medium",
             "Must curate differentiated answer unique to this mall; NOT generic luxury brand"),
            ("7.12", "put together a luxury day plan — i'll arrive at noon and leave around 8pm",
             "hybrid_plan", "high",
             "Must produce 8-hour premium itinerary: shopping → styling → lunch → beauty → experiential → dinner"),
        ],
    },

    # ── S8: Health & Wellness Enthusiast ─────────────────────────────────
    {
        "id": "S8",
        "label": "Health & Wellness Enthusiast",
        "turns": [
            ("8.1",  "i'm really into fitness and healthy eating — what's here for me?",
             "context_acknowledgement", "high",
             "Must acknowledge wellness/fitness profile; offer 3 directions; NOT standard restaurants"),
            ("8.2",  "what healthy food options do you have — i mean actually healthy, not just a salad",
             "guided_recommendation", "high",
             "Must surface genuinely health-conscious options; NOT fast food with salad caveat"),
            ("8.3",  "any options with high protein focus?",
             "guided_recommendation", "high",
             "Must refine to protein-focused options; NOT suggest burger as high-protein"),
            ("8.4",  "i'm also vegan — does that narrow it down much?",
             "guided_recommendation", "high",
             "Must immediately re-filter for vegan; NOT suggest animal products as primary"),
            ("8.5",  "so what's left that's both vegan and high-protein?",
             "guided_recommendation", "high",
             "Must surface options meeting BOTH constraints; if limited say so honestly"),
            ("8.6",  "where can i find good activewear — not just one brand, I want a few choices",
             "guided_recommendation", "high",
             "Must list multiple activewear brands/stores; NOT suggest single store"),
            ("8.7",  "i specifically want women's running gear and yoga pants",
             "guided_recommendation", "high",
             "Must refine to women's performance categories; NOT men's or general fashion"),
            ("8.8",  "do you have any supplement or nutrition stores?",
             "direct_factual", "high",
             "Must answer yes/no with location; NOT redirect to pharmacy without sports nutrition context"),
            ("8.9",  "i want to grab a post-workout smoothie — any good spots?",
             "guided_recommendation", "high",
             "Must suggest smoothie/juice bars; vegan-compatible; NOT dairy-heavy without flagging"),
            ("8.10", "something with no added sugar",
             "guided_recommendation", "medium",
             "Must refine toward no-added-sugar; NOT present same recommendations unchanged"),
            ("8.11", "what's the best approach if i want to do a 'clean' meal before a workout session?",
             "guided_recommendation", "medium",
             "Must give practical pre-workout guidance (light, vegan-compatible); NOT prescribe medical diet"),
            ("8.12", "is there a gym or fitness studio in or near the mall?",
             "direct_factual", "medium",
             "Must answer based on known data; if no gym say so; NOT invent one"),
            ("8.13", "give me a health-focused plan for my 2 hours here",
             "hybrid_plan", "high",
             "Must build wellness 2-hour plan: vegan, high-protein focused throughout"),
        ],
    },

    # ── S9: Teenage Group Hangout ─────────────────────────────────────────
    {
        "id": "S9",
        "label": "Teenage Group Hangout (Budget-Conscious, Social)",
        "turns": [
            ("9.1",  "we're a group of 5 teens just hanging out — what's fun to do here?",
             "best_effort_shortlist", "high",
             "Must acknowledge teen group; surface entertainment; NOT toddler/family-centric activities"),
            ("9.2",  "we have like 300 SAR between us — so what can we actually afford?",
             "guided_recommendation", "medium",
             "Must frame suggestions within 300 SAR total (60 SAR pp); NOT recommend expensive options"),
            ("9.3",  "what's the most fun thing for that budget?",
             "guided_recommendation", "medium",
             "Must give ONE confident recommendation with value-for-fun reasoning; NOT 'it depends'"),
            ("9.4",  "does the cinema have any good movies right now?",
             "direct_factual", "high",
             "Must return current movie schedule; highlight teen-popular films"),
            ("9.5",  "how much is a ticket for 5 people?",
             "direct_factual", "medium",
             "Must give honest pricing if known; NOT hallucinate price; direct to cinema desk if unknown"),
            ("9.6",  "we want something to eat but nothing too expensive — decent food for around 50–60 SAR each",
             "guided_recommendation", "high",
             "Must surface casual teen-friendly food at stated price range; NOT fine dining"),
            ("9.7",  "somewhere we can sit together as a group of 5 and it won't be awkward",
             "guided_recommendation", "high",
             "Must prioritise group seating and casual/social atmosphere; NOT small tables or formal"),
            ("9.8",  "any challenges or competitions we can do together?",
             "guided_recommendation", "high",
             "Must suggest competitive activities for 5; NOT activities excluding group play"),
            ("9.9",  "what's the cheapest of those?",
             "guided_recommendation", "medium",
             "Must give clear cost comparison; give 'cheapest option is X' answer; NOT hedge"),
            ("9.10", "is there anywhere to just sit and chill if we're tired?",
             "guided_recommendation", "medium",
             "Must suggest low-key areas: food court, casual café, open lounge; NOT cinema"),
            ("9.11", "what if we want to take photos together — any cool backdrops here?",
             "guided_recommendation", "medium",
             "Must suggest photogenic spots; match teen energy; NOT generic 'the mall is nice'"),
            ("9.12", "can we fit in movies, food, and one activity in 3 hours?",
             "hybrid_plan", "high",
             "Must give honest assessment; produce realistic plan or suggest prioritisation"),
            ("9.13", "suggest the best possible 3-hour hangout plan for 5 teens on 300 SAR total",
             "hybrid_plan", "high",
             "Must produce fun budget-conscious 3-hour plan; food + activity + chill; NOT exceed 300 SAR"),
        ],
    },

    # ── S10: Pre-Wedding Bridal Party ─────────────────────────────────────
    {
        "id": "S10",
        "label": "Pre-Wedding Bridal Party Shopping Spree",
        "turns": [
            ("10.1",  "we're a bridal party — the wedding is in 3 days and we're doing our final shopping day",
             "context_acknowledgement", "high",
             "Must acknowledge bridal occasion warmly; offer structured next steps"),
            ("10.2",  "the bride needs something to wear to the rehearsal dinner — elegant but not the wedding dress",
             "guided_recommendation", "high",
             "Must suggest elegant occasion-appropriate women's fashion; NOT bridal/wedding dress stores"),
            ("10.3",  "she has a specific colour in mind — dusty rose",
             "guided_recommendation", "high",
             "Must acknowledge colour preference and refine; direct to stores likely to carry that palette"),
            ("10.4",  "the 4 bridesmaids need matching accessories — not too matchy-matchy, more coordinated",
             "guided_recommendation", "high",
             "Must suggest accessories stores with variety; understand 'coordinated not identical'"),
            ("10.5",  "budget for accessories is about 200 SAR per bridesmaid",
             "guided_recommendation", "medium",
             "Must apply per-person budget; surface options within that range; NOT high-end jewellery first"),
            ("10.6",  "we all want to get a blow-dry and makeup done — where can we all go?",
             "guided_recommendation", "high",
             "Must surface salons capable of group of 5; flag appointment booking essential"),
            ("10.7",  "we need to be done by 2pm — is there a salon that can take all 5 of us at once?",
             "direct_factual", "high",
             "Must answer honestly; direct to salon to confirm; NOT confirm availability without data"),
            ("10.8",  "the bride also wants a gift from all of us — something she'll remember",
             "guided_recommendation", "high",
             "Must suggest memorable personal gift: jewellery, keepsake, personalised; NOT generic vouchers"),
            ("10.9",  "she loves oud perfume — anything premium here?",
             "guided_recommendation", "high",
             "Must surface premium oud/fragrance options; NOT generic perfume counter"),
            ("10.10", "after the shopping we want to celebrate — a nice dinner for 5, feels special",
             "guided_recommendation", "high",
             "Must suggest celebratory group-capable elegant dining; NOT food court"),
            ("10.11", "can we get a private space or at least be seated together without being split up?",
             "guided_recommendation", "high",
             "Must prioritise venues with confirmed group seating; flag advance booking essential"),
            ("10.12", "we also want to find something fun to do after dinner — the bride wants to keep the celebration going",
             "guided_recommendation", "medium",
             "Must suggest celebratory post-dinner options; NOT passive options as primary"),
            ("10.13", "can you map out a full bridal party day plan?",
             "hybrid_plan", "high",
             "Must produce well-sequenced full-day: shopping → beauty → gift → dinner → post-dinner"),
            ("10.14", "one of the bridesmaids can't wear any fragrance — she's allergic — will that affect anything?",
             "guided_recommendation", "high",
             "Must acknowledge allergy and advise caution; NOT dismiss; advise to alert salon staff"),
        ],
    },
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def send_message(session_id: str, message: str, client: httpx.Client) -> dict:
    payload = {"message": message, "session_id": session_id, "debug": True}
    resp = client.post(BASE_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def extract_debug(data: dict) -> dict:
    debug = data.get("debug") or {}
    scene = debug.get("scene_summary") or {}
    return {
        "response_mode":    debug.get("response_mode", "—"),
        "confidence_level": debug.get("confidence_level", "—"),
        "flow_type":        debug.get("flow_type", "—"),
        "chosen_strategy":  debug.get("chosen_strategy", "—"),
        "constraint_stack": scene.get("constraint_stack", []),
        "topic_lock":       scene.get("topic_lock", "—"),
        "group_size":       scene.get("group_size", "—"),
        "occasion":         scene.get("occasion", "—"),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict) -> dict:
    sid      = scenario["id"]
    label    = scenario["label"]
    turns    = scenario["turns"]
    session  = f"cx-{sid.lower()}-{_RUN_ID}"

    print(f"\n{'='*72}")
    print(f"  {sid}: {label}")
    print(f"  Session: {session}")
    print(f"{'='*72}")

    results = []
    client  = httpx.Client(timeout=TIMEOUT)

    for (turn_id, query, exp_mode, exp_conf, note) in turns:
        full_id = f"S{sid[1:]}.{turn_id}"
        try:
            t0      = time.time()
            data    = send_message(session, query, client)
            elapsed = (time.time() - t0) * 1000
            reply   = data.get("reply", data.get("message", ""))
            dbg     = extract_debug(data)

            mode_ok = dbg["response_mode"]    == exp_mode
            conf_ok = dbg["confidence_level"] == exp_conf
            passed  = mode_ok and conf_ok

            results.append({
                "turn_id":         full_id,
                "query":           query,
                "status":          "PASS" if passed else "FAIL",
                "mode_ok":         mode_ok,
                "conf_ok":         conf_ok,
                "expected_mode":   exp_mode,
                "actual_mode":     dbg["response_mode"],
                "expected_conf":   exp_conf,
                "actual_conf":     dbg["confidence_level"],
                "flow_type":       dbg["flow_type"],
                "chosen_strategy": dbg["chosen_strategy"],
                "constraint_stack": dbg["constraint_stack"],
                "topic_lock":      dbg["topic_lock"],
                "latency_ms":      round(elapsed),
                "reply_snippet":   reply[:200] if reply else "",
                "note":            note,
            })

            icon = "✅" if passed else "❌"
            print(f"\n{icon} [{full_id}] {query!r}")
            print(f"   Mode:       {dbg['response_mode']!r:30s} (expected {exp_mode!r}) {'✓' if mode_ok else '✗'}")
            print(f"   Confidence: {dbg['confidence_level']!r:30s} (expected {exp_conf!r}) {'✓' if conf_ok else '✗'}")
            print(f"   Latency:    {round(elapsed)}ms")
            print(f"   Reply:      {reply[:120]!r}")

        except Exception as exc:
            results.append({
                "turn_id":       full_id,
                "query":         query,
                "status":        "ERROR",
                "error":         str(exc),
                "expected_mode": exp_mode,
                "expected_conf": exp_conf,
                "note":          note,
            })
            print(f"\n💥 [{full_id}] {query!r}")
            print(f"   ERROR: {exc}")

    client.close()

    n_pass  = sum(1 for r in results if r["status"] == "PASS")
    n_fail  = sum(1 for r in results if r["status"] == "FAIL")
    n_error = sum(1 for r in results if r["status"] == "ERROR")
    pct     = round(n_pass / len(results) * 100) if results else 0
    print(f"\n  → {sid} result: {n_pass}/{len(results)} PASS ({pct}%)  |  fail={n_fail}  error={n_error}")

    return {
        "id":     sid,
        "label":  label,
        "session": session,
        "turns":  results,
        "n_pass": n_pass, "n_fail": n_fail, "n_error": n_error,
        "n_total": len(results),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_results(scenario_results: list) -> str:
    all_turns   = [t for s in scenario_results for t in s["turns"]]
    total       = len(all_turns)
    passed      = sum(1 for t in all_turns if t["status"] == "PASS")
    failed      = sum(1 for t in all_turns if t["status"] == "FAIL")
    errors      = sum(1 for t in all_turns if t["status"] == "ERROR")
    pct         = round(passed / total * 100) if total else 0
    avg_ms      = round(
        sum(t.get("latency_ms", 0) for t in all_turns if "latency_ms" in t)
        / max(1, sum(1 for t in all_turns if "latency_ms" in t))
    )

    now_utc  = datetime.now(timezone.utc)
    ts_label = now_utc.strftime("%Y-%m-%d_%H-%M-%S")
    ts_human = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    script_dir  = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.normpath(os.path.join(script_dir, "..", "..", "test-results"))
    os.makedirs(results_dir, exist_ok=True)

    md_path   = os.path.join(results_dir, f"complex_run_{ts_label}.md")
    json_path = os.path.join(results_dir, f"complex_run_{ts_label}.json")

    # ── Markdown ──────────────────────────────────────────────────────────
    lines = [
        "# Cenomi Chatbot — Complex Scenario Test Results",
        "",
        f"**Run date:** {ts_human}  ",
        f"**Target:** `{BASE_URL}`  ",
        f"**Session prefix:** `{_RUN_ID}`",
        "",
        "## Overall Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total turns | {total} |",
        f"| ✅ Passed | {passed} ({pct}%) |",
        f"| ❌ Failed | {failed} |",
        f"| 💥 Errors | {errors} |",
        f"| Avg latency | {avg_ms} ms |",
        "",
        "## Per-Scenario Summary",
        "",
        "| Scenario | Label | Turns | Pass | Fail | Error | Pass% |",
        "|----------|-------|-------|------|------|-------|-------|",
    ]
    for s in scenario_results:
        pct_s = round(s["n_pass"] / s["n_total"] * 100) if s["n_total"] else 0
        lines.append(
            f"| {s['id']} | {s['label']} "
            f"| {s['n_total']} | {s['n_pass']} | {s['n_fail']} | {s['n_error']} | {pct_s}% |"
        )

    # Failures table
    failures = [t for t in all_turns if t["status"] in ("FAIL", "ERROR")]
    if failures:
        lines += [
            "",
            "## Failures & Errors",
            "",
            "| Turn | Query | Expected mode | Actual mode | Expected conf | Actual conf | Note |",
            "|------|-------|--------------|-------------|--------------|-------------|------|",
        ]
        for t in failures:
            q    = t["query"][:60]
            note = t.get("note", "")[:60]
            if t["status"] == "FAIL":
                lines.append(
                    f"| {t['turn_id']} | `{q}` "
                    f"| `{t['expected_mode']}` | `{t['actual_mode']}` "
                    f"| `{t['expected_conf']}` | `{t['actual_conf']}` "
                    f"| {note} |"
                )
            else:
                lines.append(
                    f"| {t['turn_id']} | `{q}` "
                    f"| — | ERROR | — | — "
                    f"| `{t.get('error', '')}` |"
                )

    # Per-scenario detailed tables
    for s in scenario_results:
        lines += [
            "",
            f"## Scenario {s['id']}: {s['label']}",
            "",
            f"**Session:** `{s['session']}`  ",
            f"**Result:** {s['n_pass']}/{s['n_total']} PASS ({round(s['n_pass']/s['n_total']*100) if s['n_total'] else 0}%)",
            "",
            "| Turn | Query | Status | Mode | Conf | Depth | Constraints | Latency |",
            "|------|-------|--------|------|------|-------|-------------|---------|",
        ]
        for t in s["turns"]:
            icon  = "✅" if t["status"] == "PASS" else ("❌" if t["status"] == "FAIL" else "💥")
            mode  = t.get("actual_mode",  t.get("error", "—"))
            conf  = t.get("actual_conf",  "—")
            lat   = f"{t.get('latency_ms', '—')}ms"
            q     = t["query"][:55]
            constraints = ", ".join(t.get("constraint_stack", [])) if t.get("constraint_stack") else "—"
            depth = t.get("context_turn_depth", "—")
            lines.append(
                f"| {t['turn_id']} | `{q}` | {icon} | `{mode}` | `{conf}` "
                f"| {depth} | {constraints} | {lat} |"
            )

        lines += ["", "**Reply snippets:**", ""]
        for t in s["turns"]:
            snippet = t.get("reply_snippet", "")
            if snippet:
                safe = snippet.replace("|", "\\|").replace("\n", " ")[:140]
                lines.append(f"- **{t['turn_id']}** `{t['query'][:55]}` → {safe}")

    # Integrity checklist
    lines += [
        "",
        "## Conversation Integrity Checklist",
        "",
        "Review the above results against these criteria:",
        "",
        "- [ ] **Constraint persistence** — Every constraint from turn N is still respected in turn N+8",
        "- [ ] **No context reset** — Bot never forgets companion, occasion, group size mid-conversation",
        "- [ ] **No hallucination under pressure** — Prices/menu items/hours: data or honest uncertainty; never invented",
        "- [ ] **Tone consistency** — Luxury (S7, S10) = concierge; Teen (S9) = casual; Tourist (S3) = guide",
        "- [ ] **Budget guard** — Suggestions after budget declared respect that budget",
        "- [ ] **Allergy/dietary hard stops** — No suggestion contradicts a stated dietary restriction or allergy",
        "- [ ] **Honest 'I don't know'** — Bot admits missing info rather than speculating",
        "- [ ] **Plan coherence** — hybrid_plan responses consistent with full conversation history",
    ]

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ── JSON ──────────────────────────────────────────────────────────────
    payload = {
        "run_timestamp": ts_human,
        "run_id":        _RUN_ID,
        "target":        BASE_URL,
        "summary": {
            "total": total, "passed": passed, "failed": failed,
            "errors": errors, "pass_pct": pct, "avg_latency_ms": avg_ms,
        },
        "scenarios": scenario_results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"\n  Results written to:")
    print(f"    📄 {md_path}")
    print(f"    🗂  {json_path}")
    return md_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\nCenomi Chatbot — Complex Scenario Test Runner")
    print(f"Target : {BASE_URL}")
    print(f"Run ID : {_RUN_ID}\n")

    all_scenario_results = []
    for scenario in SCENARIOS:
        result = run_scenario(scenario)
        all_scenario_results.append(result)

    # Final summary
    all_turns = [t for s in all_scenario_results for t in s["turns"]]
    total  = len(all_turns)
    passed = sum(1 for t in all_turns if t["status"] == "PASS")
    failed = sum(1 for t in all_turns if t["status"] == "FAIL")
    errors = sum(1 for t in all_turns if t["status"] == "ERROR")

    print(f"\n{'='*72}")
    print(f"  OVERALL SUMMARY")
    print(f"{'='*72}")
    print(f"  Total turns : {total}")
    print(f"  Passed      : {passed} ({round(passed/total*100) if total else 0}%)")
    print(f"  Failed      : {failed}")
    print(f"  Errors      : {errors}")
    print(f"{'='*72}\n")

    for s in all_scenario_results:
        pct_s = round(s["n_pass"] / s["n_total"] * 100) if s["n_total"] else 0
        bar   = "█" * (pct_s // 10) + "░" * (10 - pct_s // 10)
        print(f"  {s['id']:4s}  [{bar}] {pct_s:3d}%  {s['n_pass']}/{s['n_total']}  {s['label']}")

    print()
    write_results(all_scenario_results)
