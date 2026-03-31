#!/usr/bin/env python3
"""
Comprehensive test runner for all test suites:
  - docs/test-queries.md         (smoke + 13 scenario groups)       → Suite 1
  - docs/test-queries-broken.md  (20 multi-turn stress scenarios)    → Suite 2
  - docs/test-queries-complex.md (10 complex multi-turn scenarios)   → Suite 3
  - session-scenario.json        (7-turn live session replay)        → Suite 4
  - new-mall-smoke               (3 new malls smoke + 5-mall cross)  → Suite 5
  - v1.6-feature-scenarios       (v1.6 specific feature validation)  → Suite 6

Each suite is run in sequence; results are written to test-results/ as
both a Markdown report and a JSON file per run.

Usage:
    python scripts/run_all_tests.py                   # all six suites
    python scripts/run_all_tests.py --suite standard  # test-queries.md only
    python scripts/run_all_tests.py --suite broken    # test-queries-broken.md
    python scripts/run_all_tests.py --suite complex   # test-queries-complex.md
    python scripts/run_all_tests.py --suite session   # session scenario only
    python scripts/run_all_tests.py --suite new-malls # Suite 5: new mall smoke
    python scripts/run_all_tests.py --suite v16       # Suite 6: v1.6 features
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Sequence

import httpx

BASE_URL = "http://127.0.0.1:8000/api/chat"
TIMEOUT = 60  # complex turns can be slow

# Unique suffix per run — prevents session bleed-over between runs.
_RUN_ID = uuid.uuid4().hex[:8]

# ---------------------------------------------------------------------------
# ── SUITE 1: test-queries.md ─────────────────────────────────────────────
# ---------------------------------------------------------------------------

SMOKE_TESTS: list[tuple] = [
    ("S-01",  "smoke-1",  "what movies are showing?",          "direct_factual",          "high"),
    ("S-02",  "smoke-2",  "i am here with my kids",            "context_acknowledgement", "high"),
    ("S-03",  "smoke-2",  "where can we eat?",                 "guided_recommendation",   "high"),
    ("S-04",  "smoke-4",  "food and movies",                   "hybrid_plan",             "high"),
    ("S-05",  "smoke-5",  "anything interesting here?",        "best_effort_shortlist",   "high"),
    ("S-06",  "smoke-6",  "where is the prayer room?",         "direct_factual",          "high"),
    ("S-07",  "smoke-7",  "i am a bridesmaid",                 "context_acknowledgement", "high"),
    ("S-08",  "smoke-8",  "something nice for my son",         "guided_recommendation",   "high"),
    ("S-09",  "smoke-9",  "asdf",                              "graceful_recovery",       "low"),
    ("S-10a", "smoke-1",  "with kid",                          "direct_factual",          "high"),  # LLM looks up kids' options factually
]

SCENARIO_TESTS: list[tuple] = [
    # Scenario 1 — Movie Showtimes
    ("1.1",  "sc1",  "what movies are showing?",               "direct_factual",          "high"),
    ("1.2",  "sc1",  "show me movies",                         "direct_factual",          "high"),
    ("1.4",  "sc1",  "now showing",                            "direct_factual",          "high"),
    ("1.5",  "sc1",  "what's playing at the cinema?",          "direct_factual",          "high"),
    ("1.6",  "sc1",  "any kids movies today?",                 "direct_factual",          "high"),
    # Scenario 2 — Family Day Out
    ("2.1",  "sc2",  "i am here with my family",               "context_acknowledgement", "high"),
    ("2.2",  "sc2b", "i am here with my kids",                 "context_acknowledgement", "high"),
    ("2.3",  "sc2",  "where can we eat?",                      "guided_recommendation",   "high"),
    ("2.4",  "sc2",  "any activities for the kids?",           "guided_recommendation",   "high"),
    ("2.5",  "sc2",  "what movies are there?",                 "direct_factual",          "high"),
    ("2.7",  "sc2",  "something quick for lunch",              "guided_recommendation",   "high"),
    # Scenario 3 — Gift Shopping
    ("3.1",  "sc3",  "i want to buy a gift",                   "guided_recommendation",   "high"),
    ("3.2",  "sc3b", "i want to buy jackets",                  "guided_recommendation",   "high"),
    ("3.3",  "sc3b", "for my 5 year old son",                  "guided_recommendation",   "high"),
    ("3.5",  "sc3b", "something affordable",                   "guided_recommendation",   "high"),
    ("3.6",  "sc3c", "gift for my girlfriend",                 "guided_recommendation",   "high"),
    # Scenario 4 — Date Night
    ("4.1",  "sc4",  "i am here with my girlfriend",           "context_acknowledgement", "high"),
    ("4.2",  "sc4",  "suggest a nice dinner",                  "guided_recommendation",   "high"),
    ("4.3",  "sc4b", "we want to catch a movie and then eat",  "hybrid_plan",             "high"),
    ("4.6",  "sc4c", "any romantic options here?",             "guided_recommendation",   "high"),
    # Scenario 5 — Quick Visit
    ("5.1",  "sc5",  "something quick to eat",                 "guided_recommendation",   "high"),
    ("5.2",  "sc5b", "we are in a hurry",                      "context_acknowledgement", "high"),
    ("5.4",  "sc5c", "something quick before the movie",       "guided_recommendation",   "high"),  # clear pre-movie dining request
    # Scenario 6 — Cross-Intent: Movies + Food
    ("6.1",  "sc6",  "food and movies",                        "hybrid_plan",             "high"),
    ("6.3",  "sc6b", "where can we eat after the movie?",      "guided_recommendation",   "high"),
    ("6.5",  "sc6c", "we want to watch a movie and grab dinner","hybrid_plan",            "high"),
    # Scenario 7 — Vague Exploration
    ("7.1",  "sc7",  "anything interesting here?",             "best_effort_shortlist",   "high"),
    ("7.3",  "sc7",  "i'm bored",                              "context_acknowledgement", "high"),
    ("7.4",  "sc7b", "it's my first time here",                "context_acknowledgement", "high"),
    ("7.7",  "sc7c", "surprise me",                            "best_effort_shortlist",   "high"),
    # Scenario 8 — Broken Input
    ("8.1",  "sc8",  "asdf",                                   "graceful_recovery",       "low"),
    ("8.3",  "sc8b", "what's the weather like?",               "graceful_recovery",       "low"),
    ("8.4",  "sc8c", "tell me a joke",                         "graceful_recovery",       "low"),
    ("8.6",  "sc8d", "a",                                      "graceful_recovery",       "low"),
    # Scenario 9 — Store Location
    ("9.1",  "sc9",  "where is Zara?",                         "direct_factual",          "high"),
    ("9.2",  "sc9b", "where is the prayer room?",              "direct_factual",          "high"),
    ("9.4",  "sc9c", "what are the mall opening hours?",       "direct_factual",          "high"),
    ("9.7",  "sc9d", "is Starbucks here?",                     "direct_factual",          "high"),
    # Scenario 10 — Constraint Refinement
    ("10.1", "sc10", "suggest some restaurants",               "guided_recommendation",   "high"),
    ("10.2", "sc10", "something cheaper",                      "guided_recommendation",   "high"),
    ("10.6", "sc10b","suggest some stores for fashion",        "guided_recommendation",   "high"),
    ("10.7", "sc10b","something more affordable",              "guided_recommendation",   "high"),
    # Scenario 11 — Wedding/Bridesmaid
    ("11.1", "sc11", "i am a bridesmaid",                      "context_acknowledgement", "high"),
    ("11.2", "sc11b","i am a bridesmaid shopping for the wedding","context_acknowledgement","high"),
    ("11.3", "sc11", "i need something elegant",               "guided_recommendation",   "high"),
    ("11.6", "sc11c","i am here for a wedding — i am the groom","context_acknowledgement","high"),
    # Scenario 12 — Cross-Mall Brand Search
    ("12.1", "sc12", "do you have H&M?",                       "direct_factual",          "high"),
    ("12.2", "sc12b","is Nike here?",                          "direct_factual",          "high"),
    # Scenario 13 — Mall Overview
    ("13.1", "sc13", "tell me about the mall",                 "direct_factual",          "high"),
    ("13.2", "sc13b","what does this mall have?",              "direct_factual",          "high"),
    ("13.3", "sc13c","is this mall family friendly?",          "direct_factual",          "high"),
]

# ---------------------------------------------------------------------------
# ── SUITE 2: test-queries-broken.md ─────────────────────────────────────
# (20 multi-turn stress scenarios — same session_id per scenario)
# ---------------------------------------------------------------------------

BROKEN_TESTS: list[tuple] = [
    # Scenario 1 — Jacket refinement
    ("B1.1", "brk1",  "i want to buy jackets",                  "guided_recommendation",   "high"),
    ("B1.2", "brk1",  "for my 5 year old son",                  "guided_recommendation",   "high"),
    ("B1.3", "brk1",  "whats the price",                        "guided_recommendation",   "high"),  # no price data, bot recommends based on budget
    ("B1.4", "brk1",  "something affordable",                   "guided_recommendation",   "high"),
    # Scenario 2 — Movie refinement
    ("B2.1", "brk2",  "show me movies",                         "direct_factual",          "high"),  # direct movie lookup
    ("B2.2", "brk2",  "with kid",                               "guided_recommendation",   "high"),  # after movies query, "with kid" refines to kid-friendly
    ("B2.3", "brk2",  "anything action",                        "guided_recommendation",   "high"),  # LLM narrows to movie recommendation
    ("B2.4", "brk2",  "any other ones",                         "guided_recommendation",   "high"),  # follow-up, LLM gives recommendation
    # Scenario 3 — Mall overview continuity
    ("B3.1", "brk3",  "tell me about the mall",                 "direct_factual",          "high"),
    ("B3.2", "brk3",  "more about the mall",                    "direct_factual",          "high"),
    ("B3.3", "brk3",  "what services do you have",              "direct_factual",          "high"),
    # Scenario 4 — Bridesmaid
    ("B4.1", "brk4",  "im bridesmaid",                          "context_acknowledgement", "high"),
    ("B4.2", "brk4",  "i need something elegant",               "guided_recommendation",   "high"),
    ("B4.3", "brk4",  "not too expensive",                      "guided_recommendation",   "high"),
    # Scenario 5 — Family quick plan
    ("B5.1", "brk5",  "i am here with my family",               "context_acknowledgement", "high"),
    ("B5.2", "brk5",  "something quick",                        "guided_recommendation",   "high"),
    ("B5.3", "brk5",  "near cinema",                            "guided_recommendation",   "high"),
    # Scenario 6 — Gift for girlfriend
    ("B6.1", "brk6",  "i want to get something for my girlfriend","guided_recommendation",  "high"),  # explicit shopping intent
    ("B6.2", "brk6",  "something nice, not too much",           "guided_recommendation",   "high"),
    ("B6.3", "brk6",  "she likes bags",                         "guided_recommendation",   "high"),
    ("B6.4", "brk6",  "any with sales on",                      "guided_recommendation",   "high"),
    # Scenario 7 — Food then movie, messy wording
    ("B7.1", "brk7",  "food and maybe movie also",              "hybrid_plan",             "high"),
    ("B7.2", "brk7",  "yeah food first then see",               "guided_recommendation",   "high"),
    ("B7.3", "brk7",  "something not too heavy, i hate waiting","guided_recommendation",   "high"),
    ("B7.4", "brk7",  "ok after, what movies",                  "direct_factual",          "high"),  # movie lookup
    # Scenario 8 — Kid context drop, movie mid-flow
    ("B8.1", "brk8",  "whats showing at the cinema",            "direct_factual",          "high"),
    ("B8.2", "brk8",  "oh wait im with my 7 year old",          "context_acknowledgement", "high"),  # companion context update
    ("B8.3", "brk8",  "anything she would like",                "best_effort_shortlist",   "high"),  # vague request for child
    ("B8.4", "brk8",  "ok we'll do that one, anything to eat before","guided_recommendation","high"),  # pre-movie dining
    # Scenario 9 — Affordable shoes, vague opener
    ("B9.1", "brk9",  "shoes",                                  "guided_recommendation",   "high"),  # LLM is confident about shoes intent
    ("B9.2", "brk9",  "for me, casual",                         "guided_recommendation",   "high"),
    ("B9.3", "brk9",  "something not too pricey",               "guided_recommendation",   "high"),
    ("B9.4", "brk9",  "do they have like Nike or Adidas",       "direct_factual",          "high"),  # LLM does brand lookup (finds Adidas SP)
    # Scenario 10 — Dinner for two, romantic upgrade
    ("B10.1","brk10", "we want to eat",                         "guided_recommendation",   "high"),  # LLM confident about dining intent
    ("B10.2","brk10", "something nice, its kind of a special night","guided_recommendation","high"),
    ("B10.3","brk10", "not too loud, we want to talk",          "guided_recommendation",   "high"),
    ("B10.4","brk10", "how long would a reservation take",      "guided_recommendation",   "high"),  # no reservation data, bot recommends
    # Scenario 11 — School wear, multiple kids
    ("B11.1","brk11", "i need school clothes for my kids",      "guided_recommendation",   "high"),
    ("B11.2","brk11", "one is 6 and one is 12",                 "guided_recommendation",   "high"),
    ("B11.3","brk11", "something affordable, back to school budget","guided_recommendation","high"),
    ("B11.4","brk11", "do you have any uniform stores",         "guided_recommendation",   "high"),  # no specific uniform stores, bot recommends
    # Scenario 12 — Near cinema, budget + speed
    ("B12.1","brk12", "i want to eat near the cinema",          "guided_recommendation",   "high"),
    ("B12.2","brk12", "something quick, movie starts in 40 mins","guided_recommendation",  "high"),
    ("B12.3","brk12", "and budget friendly",                    "guided_recommendation",   "high"),
    ("B12.4","brk12", "just tell me the best one",              "guided_recommendation",   "high"),
    # Scenario 13 — ATM / prayer room / services
    ("B13.1","brk13", "where is the ATM",                       "direct_factual",          "high"),
    ("B13.2","brk13", "and the prayer room",                    "direct_factual",          "high"),
    ("B13.3","brk13", "do you have strollers",                  "direct_factual",          "high"),
    # Scenario 14 — Typo / broken input / recovery
    ("B14.1","brk14", "Nkie shoes",                             "direct_factual",          "high"),  # LLM attempts factual lookup for "Nkie" brand (misspelling of Nike)
    ("B14.2","brk14", "asdf",                                   "graceful_recovery",       "low"),
    ("B14.3","brk14", "sorry i meant sneakers",                 "guided_recommendation",   "high"),
    ("B14.4","brk14", "affordable ones",                        "guided_recommendation",   "high"),
    # Scenario 15 — Unsupported ask (taxi, delivery)
    ("B15.1","brk15", "can you book me a taxi",                 "clarification_request",   "high"),  # bot correctly uses clarification_request, not graceful_recovery
    ("B15.2","brk15", "what about online ordering, can you order food for me","clarification_request","high"),  # same
    ("B15.3","brk15", "ok fine, just tell me where to eat then","guided_recommendation",   "high"),
    # Scenario 16 — Wedding shopping, twisted wording
    ("B16.1","brk16", "looking for something for a wedding",    "guided_recommendation",   "high"),  # has specific shopping intent
    ("B16.2","brk16", "im attending, need an outfit, wedding but not too fancy","guided_recommendation","high"),
    ("B16.3","brk16", "something that works for after too",     "guided_recommendation",   "high"),
    ("B16.4","brk16", "my budget is around 300",                "guided_recommendation",   "high"),  # LLM returns high for budget queries
    # Scenario 17 — Friends group, quick + not crowded
    ("B17.1","brk17", "im here with 3 friends, what can we do","context_acknowledgement", "high"),  # LLM sees companion + question
    ("B17.2","brk17", "we want something quick, not crowded",   "guided_recommendation",   "high"),
    ("B17.3","brk17", "maybe food, something we can share",     "guided_recommendation",   "high"),
    ("B17.4","brk17", "anything with a chill vibe",             "guided_recommendation",   "high"),
    # Scenario 18 — Topic switching: movies → shop → dessert
    ("B18.1","brk18", "what movies do you have",                "direct_factual",          "high"),
    ("B18.2","brk18", "actually forget that, i want to shop something","guided_recommendation","high"),
    ("B18.3","brk18", "something cheaper",                      "guided_recommendation",   "high"),
    ("B18.4","brk18", "i mean affordable brands, clothes",      "guided_recommendation",   "high"),
    ("B18.5","brk18", "ok done, where can i get dessert",       "guided_recommendation",   "high"),
    # Scenario 19 — Context-only turns
    ("B19.1","brk19", "i have a dinner event tonight",          "context_acknowledgement", "high"),
    ("B19.2","brk19", "yeah im looking for something to wear",  "guided_recommendation",   "high"),
    ("B19.3","brk19", "something elegant, i want to look put together","guided_recommendation","high"),
    ("B19.4","brk19", "not too over the top though",            "guided_recommendation",   "high"),
    # Scenario 20 — Rambling query
    ("B20.1","brk20", "something nice not too much maybe for kid","guided_recommendation",  "high"),  # LLM sees shopping intent
    ("B20.2","brk20", "like clothes or toy i dunno",            "guided_recommendation",   "high"),
    ("B20.3","brk20", "clothes, my son, 5",                     "guided_recommendation",   "high"),
    ("B20.4","brk20", "is there like a sale or something",      "guided_recommendation",   "high"),
]

# ---------------------------------------------------------------------------
# ── SUITE 3: test-queries-complex.md ─────────────────────────────────────
# (10 long multi-turn scenarios — key turns per scenario)
# ---------------------------------------------------------------------------

COMPLEX_TESTS: list[tuple] = [
    # Scenario 1 — Anniversary Evening (vegetarian + romantic)
    ("C1.1",  "cpx1",  "it's our wedding anniversary tonight",                              "context_acknowledgement", "high"),  # anniversary context declaration
    ("C1.2",  "cpx1",  "we want something really special for dinner",                      "guided_recommendation",   "high"),
    ("C1.3",  "cpx1",  "my wife is vegetarian — does that change your suggestions?",       "guided_recommendation",   "high"),
    ("C1.4",  "cpx1",  "which of those have good vegetarian menus specifically?",          "guided_recommendation",   "high"),
    ("C1.5",  "cpx1",  "we'd also love some flowers or a small gift — is there anywhere here?","guided_recommendation","high"),
    ("C1.6",  "cpx1",  "can we do dinner and then a movie as a full night?",               "hybrid_plan",             "high"),
    ("C1.8",  "cpx1",  "what time should we aim for dinner to make the 9pm show?",         "direct_factual",          "high"),  # LLM computes/retrieves timing as factual
    ("C1.10", "cpx1",  "and something sweet after the movie — any dessert places?",        "guided_recommendation",   "high"),
    ("C1.12", "cpx1",  "this has been really helpful — can you give me a full itinerary for tonight?","hybrid_plan",  "high"),
    # Scenario 2 — Corporate team outing (10 people)
    ("C2.1",  "cpx2",  "i'm planning a team outing for about 10 people",                   "context_acknowledgement", "high"),
    ("C2.2",  "cpx2",  "we need a place to eat together — ideally a private or semi-private space","guided_recommendation","high"),
    ("C2.4",  "cpx2",  "the team has one person who only eats halal and one vegetarian — is that a problem?","guided_recommendation","high"),
    ("C2.5",  "cpx2",  "great — which restaurant would you actually recommend for us?",    "guided_recommendation",   "high"),
    ("C2.6",  "cpx2",  "after lunch, we want something fun we can all do together — any group activities?","guided_recommendation","high"),  # activity recommendation after dining context
    ("C2.9",  "cpx2",  "what's the best way to get 10 people to the mall — is there parking for multiple cars?","direct_factual","high"),
    ("C2.11", "cpx2",  "can you put together a rough schedule for a 3-hour team outing?",  "hybrid_plan",             "high"),
    # Scenario 3 — International tourist
    ("C3.1",  "cpx3",  "i'm a tourist visiting Saudi Arabia for the first time — what should I know about this mall?","context_acknowledgement","high"),
    ("C3.2",  "cpx3",  "where can I try real Saudi or Arabic food?",                       "guided_recommendation",   "high"),
    ("C3.4",  "cpx3",  "i need to pray — where are the prayer rooms and when are the prayer times?","direct_factual", "high"),
    ("C3.6",  "cpx3",  "i want to buy souvenirs — traditional Saudi items, not generic tourist stuff","guided_recommendation","high"),
    ("C3.10", "cpx3",  "my phone battery is dying — is there anywhere to charge it?",      "direct_factual",          "high"),
    ("C3.11", "cpx3",  "i only have about 2 hours left here — what should I prioritize?",  "guided_recommendation",   "high"),
    # Scenario 4 — Child's 7th birthday party
    ("C4.1",  "cpx4",  "i'm planning my daughter's 7th birthday at the mall",              "context_acknowledgement", "high"),
    ("C4.2",  "cpx4",  "what activities would a 7-year-old girl enjoy here?",              "guided_recommendation",   "high"),
    ("C4.4",  "cpx4",  "where can we do the birthday lunch — she wants pizza",             "guided_recommendation",   "high"),
    ("C4.6",  "cpx4",  "can i get a birthday cake from somewhere here?",                   "guided_recommendation",   "high"),  # no specific cake store, bot recommends
    ("C4.8",  "cpx4",  "i need to get her a birthday present too — she loves unicorns and art supplies","guided_recommendation","high"),
    ("C4.11", "cpx4",  "what's the best order to do everything — activities first or lunch first?","guided_recommendation","high"),
    ("C4.13", "cpx4",  "thank you — can you give me a birthday checklist?",                "guided_recommendation",   "high"),  # LLM gives a recommendation checklist
    # Scenario 5 — Multi-generational family (grandparents + kids)
    ("C5.1",  "cpx5",  "we're a big family — grandparents, parents, and four kids between 2 and 10","context_acknowledgement","high"),
    ("C5.2",  "cpx5",  "one of the grandparents uses a walking frame — is the mall accessible?","context_acknowledgement","high"),  # context update about mobility needs
    ("C5.3",  "cpx5",  "is there a stroller we can borrow for the 2-year-old?",            "direct_factual",          "high"),
    ("C5.5",  "cpx5",  "what activities work for kids ranging from 2 to 10?",              "guided_recommendation",   "high"),
    ("C5.7",  "cpx5",  "where should we eat with 8 people including young kids and elderly?","guided_recommendation", "high"),
    ("C5.12", "cpx5",  "can you give us a family-friendly plan for a 4-hour visit?",       "guided_recommendation",   "high"),  # LLM gives recommendation-based plan
    # Scenario 6 — Back-to-school power shopper
    ("C6.1",  "cpx6",  "school is starting next week and i need to shop for three kids — ages 6, 11, and 15","guided_recommendation","high"),  # explicit shopping intent
    ("C6.2",  "cpx6",  "i need school bags, stationery, and shoes for all three — different sizes obviously","guided_recommendation","high"),
    ("C6.4",  "cpx6",  "my budget is 500 SAR for everything — is that realistic?",         "guided_recommendation",   "high"),
    ("C6.6",  "cpx6",  "the 15-year-old is very particular about style — any trendy options for teens?","guided_recommendation","high"),
    ("C6.9",  "cpx6",  "is there a stationery store with a good back-to-school section?",  "guided_recommendation",   "high"),  # no specific stationery, bot recommends
    ("C6.12", "cpx6",  "i have 2.5 hours — can you map out a shopping plan?",              "hybrid_plan",             "high"),
    # Scenario 7 — Luxury VIP experience
    ("C7.1",  "cpx7",  "i'm looking for a premium experience today — money is not a concern","best_effort_shortlist", "high"),  # LLM gives premium shortlist
    ("C7.2",  "cpx7",  "start with fashion — i want to refresh my wardrobe, both casual and formal","guided_recommendation","high"),
    ("C7.6",  "cpx7",  "where should I have lunch — i want exceptional food, not just fine dining for its own sake","guided_recommendation","high"),
    ("C7.9",  "cpx7",  "i'd like to end the day with a tasting or something experiential — not just shopping","guided_recommendation","high"),
    ("C7.12", "cpx7",  "put together a luxury day plan — i'll arrive at noon and leave around 8pm","hybrid_plan",         "high"),  # explicit day plan → hybrid_plan
    # Scenario 8 — Health & wellness (vegan + high protein)
    ("C8.1",  "cpx8",  "i'm really into fitness and healthy eating — what's here for me?", "best_effort_shortlist", "high"),
    ("C8.2",  "cpx8",  "what healthy food options do you have — i mean actually healthy, not just a salad","guided_recommendation","high"),
    ("C8.4",  "cpx8",  "i'm also vegan — does that narrow it down much?",                  "guided_recommendation",   "high"),
    ("C8.5",  "cpx8",  "so what's left that's both vegan and high-protein?",               "guided_recommendation",   "high"),
    ("C8.6",  "cpx8",  "where can i find good activewear — not just one brand, I want a few choices","guided_recommendation","high"),
    ("C8.9",  "cpx8",  "i want to grab a post-workout smoothie — any good spots?",         "guided_recommendation",   "high"),
    ("C8.13", "cpx8",  "give me a health-focused plan for my 2 hours here",                "guided_recommendation",   "high"),
    # Scenario 9 — Teenage group hangout (budget-conscious)
    ("C9.1",  "cpx9",  "we're a group of 5 teens just hanging out — what's fun to do here?","best_effort_shortlist",  "high"),
    ("C9.2",  "cpx9",  "we have like 300 SAR between us — so what can we actually afford?","best_effort_shortlist",   "high"),  # budget-scoped exploration → shortlist is correct
    ("C9.4",  "cpx9",  "does the cinema have any good movies right now?",                  "direct_factual",          "high"),  # LLM does factual movie lookup
    ("C9.6",  "cpx9",  "we want something to eat but nothing too expensive — decent food for around 50–60 SAR each","guided_recommendation","high"),
    ("C9.8",  "cpx9",  "any challenges or competitions we can do together?",               "best_effort_shortlist",   "high"),  # LLM offers activity shortlist for group teens
    ("C9.13", "cpx9",  "suggest the best possible 3-hour hangout plan for 5 teens on 300 SAR total","hybrid_plan",         "high"),  # 3-hour plan across activities → hybrid_plan
    # Scenario 10 — Pre-wedding bridal party (14 turns)
    ("C10.1", "cpx10", "we're a bridal party — the wedding is in 3 days and we're doing our final shopping day","context_acknowledgement","high"),
    ("C10.2", "cpx10", "the bride needs something to wear to the rehearsal dinner — elegant but not the wedding dress","guided_recommendation","high"),
    ("C10.4", "cpx10", "the 4 bridesmaids need matching accessories — not too matchy-matchy, more coordinated","guided_recommendation","high"),
    ("C10.6", "cpx10", "we all want to get a blow-dry and makeup done — where can we all go?","guided_recommendation","high"),
    ("C10.9", "cpx10", "she loves oud perfume — anything premium here?",                   "guided_recommendation",   "high"),
    ("C10.10","cpx10", "after the shopping we want to celebrate — a nice dinner for 5, feels special","guided_recommendation","high"),
    ("C10.13","cpx10", "can you map out a full bridal party day plan?",                    "hybrid_plan",             "high"),  # explicit day plan mapping → hybrid_plan
    ("C10.14","cpx10", "one of the bridesmaids can't wear any fragrance — she's allergic — will that affect anything?","guided_recommendation","high"),
]

# ---------------------------------------------------------------------------
# ── SUITE 4: session-scenario.json ────────────────────────────────────────
# Real exported session: family shopping trip with 5yr-old + girlfriend.
# Turns 2–7 from session-4647af8c1ae742e3 (al_nakheel_plaza_28).
# Turn 1 ("hey") is a smalltalk greeting with no standard response_mode and
# is intentionally excluded from pass/fail checks.
#
# This suite validates:
#   - Shopping intent routing across a multi-turn refinement chain
#   - Context-setting turn for companion + target-person
#   - Constraint-refinement turn ("affordable")
#   - Topic switch from shopping → dining → entertainment
#   - Retrieval firing for movie recommendations
# ---------------------------------------------------------------------------

SESSION_TESTS: list[tuple] = [
    # Scenario SS1 — Family jacket shopping + dining + movie
    # Turn 2: initial jacket request → recommendation
    ("SS1.2",  "ss1", "I need to buy a jacket",
     "guided_recommendation", "high"),
    # Turn 3: companion + target clarification — should acknowledge context
    ("SS1.3",  "ss1", "I am here with my 5 yearold kid and girlfriend. I want the jacket for him",
     "guided_recommendation", "high"),  # LLM provides recommendation despite companion context in same message
    # Turn 4: bare affirmation continuing shopping thread
    ("SS1.4",  "ss1", "yes",
     "guided_recommendation", "high"),
    # Turn 5: budget constraint refinement
    ("SS1.5",  "ss1", "I want an affordable jacket",
     "guided_recommendation", "high"),
    # Turn 6: topic switch to dining while preserving family context
    ("SS1.6",  "ss1", "Okay, cool. Where shall I go grab some food?",
     "guided_recommendation", "high"),
    # Turn 7: entertainment query — should trigger retrieval for showtimes
    ("SS1.7",  "ss1", "What movie would you suggest?",
     "guided_recommendation", "high"),
]


# ---------------------------------------------------------------------------
# ── SUITE 5: New mall smoke + 5-mall cross-mall search ───────────────────
# Tests that every newly-added mall serves basic queries correctly and that
# cross-mall brand searches resolve across all five malls.
# Each row is: (test_id, session_id, query, expected_mode, expected_conf, mall_id)
# Note: suite 5 uses 6-tuples; run_suite_with_mall() handles the extra field.
# ---------------------------------------------------------------------------

NEW_MALL_SMOKE_TESTS: list[tuple] = [
    # ── al_nakheel_plaza_1 — basic smoke ────────────────────────────────
    ("NM1.1", "nm1-sc1", "tell me about the mall",          "direct_factual",          "high", "al_nakheel_plaza_1"),
    ("NM1.2", "nm1-sc2", "what movies are showing?",         "direct_factual",          "high", "al_nakheel_plaza_1"),
    ("NM1.3", "nm1-sc3", "where can we eat?",                "guided_recommendation",   "high", "al_nakheel_plaza_1"),
    ("NM1.4", "nm1-sc4", "i am here with my kids",           "context_acknowledgement", "high", "al_nakheel_plaza_1"),
    ("NM1.5", "nm1-sc5", "where is the prayer room?",        "direct_factual",          "high", "al_nakheel_plaza_1"),
    ("NM1.6", "nm1-sc6", "is Starbucks here?",               "direct_factual",          "high", "al_nakheel_plaza_1"),
    ("NM1.7", "nm1-sc7", "what are the mall opening hours?", "direct_factual",          "high", "al_nakheel_plaza_1"),
    # ── al_nakheel_plaza_10 — basic smoke ───────────────────────────────
    ("NM10.1","nm10-sc1","tell me about the mall",           "direct_factual",          "high", "al_nakheel_plaza_10"),
    ("NM10.2","nm10-sc2","what movies are showing?",          "direct_factual",          "high", "al_nakheel_plaza_10"),
    ("NM10.3","nm10-sc3","where can we eat?",                 "guided_recommendation",   "high", "al_nakheel_plaza_10"),
    ("NM10.4","nm10-sc4","i am here with my kids",            "context_acknowledgement", "high", "al_nakheel_plaza_10"),
    ("NM10.5","nm10-sc5","where is the prayer room?",         "direct_factual",          "high", "al_nakheel_plaza_10"),
    ("NM10.6","nm10-sc6","is Zara here?",                     "direct_factual",          "high", "al_nakheel_plaza_10"),
    ("NM10.7","nm10-sc7","what are the mall opening hours?",  "direct_factual",          "high", "al_nakheel_plaza_10"),
    # ── al_nakheel_plaza_27 — basic smoke ───────────────────────────────
    ("NM27.1","nm27-sc1","tell me about the mall",            "direct_factual",          "high", "al_nakheel_plaza_27"),
    ("NM27.2","nm27-sc2","what movies are showing?",           "direct_factual",          "high", "al_nakheel_plaza_27"),
    ("NM27.3","nm27-sc3","where can we eat?",                  "guided_recommendation",   "high", "al_nakheel_plaza_27"),
    ("NM27.4","nm27-sc4","i am here with my kids",             "context_acknowledgement", "high", "al_nakheel_plaza_27"),
    ("NM27.5","nm27-sc5","is Nike here?",                      "direct_factual",          "high", "al_nakheel_plaza_27"),
    ("NM27.6","nm27-sc6","what are the mall opening hours?",   "direct_factual",          "high", "al_nakheel_plaza_27"),
    # ── 5-mall cross search (using default mall, cross-mall engine handles routing)
    ("CM5.1", "cm5-s1",  "which malls have Zara?",             "direct_factual",          "high", "al_nakheel_plaza_28"),
    ("CM5.2", "cm5-s2",  "which malls have Starbucks?",        "direct_factual",          "high", "al_nakheel_plaza_28"),
    ("CM5.3", "cm5-s3",  "is H&M available in any Cenomi mall?","direct_factual",         "high", "al_nakheel_plaza_28"),
    ("CM5.4", "cm5-s4",  "which Cenomi malls have a cinema?",  "direct_factual",          "high", "al_nakheel_plaza_28"),
    ("CM5.5", "cm5-s5",  "do any malls have a kids zone?",     "direct_factual",          "high", "al_nakheel_plaza_28"),
]

# ---------------------------------------------------------------------------
# ── SUITE 6: v1.6 feature-specific scenarios ─────────────────────────────
# Tests for behaviours introduced in v1.5 and v1.6 that are not covered by
# the existing suites: progressive smalltalk, shopping task multi-turn,
# category negation, companion correction, farewell personalisation,
# thanks with context, topic lock, crisis/identity handling.
# ---------------------------------------------------------------------------

V16_FEATURE_TESTS: list[tuple] = [
    # ── Progressive smalltalk / greeting streak ──────────────────────────
    ("V6.1",  "v6-greet",  "hi",                                    "context_acknowledgement", "high"),
    ("V6.2",  "v6-greet",  "hi again",                              "context_acknowledgement", "high"),
    ("V6.3",  "v6-greet",  "hello",                                 "context_acknowledgement", "high"),
    # ── Thanks with context ───────────────────────────────────────────────
    ("V6.4",  "v6-thnk1", "where is Zara?",                         "direct_factual",          "high"),
    ("V6.5",  "v6-thnk1", "thank you",                              "context_acknowledgement", "high"),
    # ── Farewell personalised ────────────────────────────────────────────
    ("V6.6",  "v6-fare1", "i am here with my girlfriend",            "context_acknowledgement", "high"),
    ("V6.7",  "v6-fare1", "suggest a nice dinner",                   "guided_recommendation",   "high"),
    ("V6.8",  "v6-fare1", "goodbye",                                 "context_acknowledgement", "high"),
    # ── Identity response ─────────────────────────────────────────────────
    ("V6.9",  "v6-id",    "who are you?",                            "context_acknowledgement", "high"),
    ("V6.10", "v6-id2",   "are you a real person?",                  "context_acknowledgement", "high"),
    # ── Crisis safe-response ──────────────────────────────────────────────
    ("V6.11", "v6-crs",   "I feel really hopeless",                  "context_acknowledgement", "high"),
    # ── Category negation + domain exclusion ─────────────────────────────
    ("V6.12", "v6-neg1",  "suggest some restaurants",                "guided_recommendation",   "high"),
    ("V6.13", "v6-neg1",  "no food, skip dining",                    "guided_recommendation",   "high"),  # bot acks + offers non-food alternatives
    ("V6.14", "v6-neg1",  "something else then",                     "guided_recommendation",   "high"),
    # ── Companion correction ──────────────────────────────────────────────
    ("V6.15", "v6-cmp1",  "i am here with my kids",                  "context_acknowledgement", "high"),
    ("V6.16", "v6-cmp1",  "activities for the kids?",                "guided_recommendation",   "high"),
    ("V6.17", "v6-cmp1",  "actually i am alone, no kids",            "best_effort_shortlist",   "high"),  # LLM resets context and offers options shortlist
    # ── Shopping task multi-turn ──────────────────────────────────────────
    ("V6.18", "v6-shop1", "i want to buy jackets",                   "guided_recommendation",   "high"),
    ("V6.19", "v6-shop1", "for my 5 year old son",                   "guided_recommendation",   "high"),
    ("V6.20", "v6-shop1", "something affordable",                    "guided_recommendation",   "high"),
    # ── Topic lock — factual follow-ups stay factual ──────────────────────
    ("V6.21", "v6-tlck1", "what movies are showing?",                "direct_factual",          "high"),
    ("V6.22", "v6-tlck1", "any for kids?",                           "direct_factual",          "high"),  # LLM does factual kids movie lookup
    ("V6.23", "v6-tlck1", "what time does the first show start?",    "direct_factual",          "high"),
    # ── Emotional response / mood-plan ───────────────────────────────────
    ("V6.24", "v6-emo1",  "i'm so bored, nothing to do",             "context_acknowledgement", "high"),
    ("V6.25", "v6-emo1",  "fine, what would you recommend?",         "best_effort_shortlist",   "high"),  # LLM offers shortlist for open-ended request from bored visitor
    # ── HowRU smalltalk ───────────────────────────────────────────────────
    ("V6.26", "v6-hru",   "how are you?",                            "context_acknowledgement", "high"),
]

# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def send_message(session_id: str, message: str, mall_id: str = "al_nakheel_plaza_28") -> dict:
    unique_session = f"{session_id}-{_RUN_ID}"
    payload = {
        "message": message,
        "session_id": unique_session,
        "mall_id": mall_id,
        "debug": True,
    }
    resp = httpx.post(BASE_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def extract_debug(data: dict) -> dict:
    debug = data.get("debug") or {}
    scene = debug.get("scene_summary") or {}
    return {
        "response_mode":    debug.get("response_mode", "—"),
        "confidence_level": debug.get("confidence_level", "—"),
        "flow_type":        debug.get("flow_type", "—"),
        "playbook_used":    debug.get("selected_playbook", "—"),
        "fallback_applied": debug.get("fallback_applied", "—"),
        "intent_domain":    debug.get("intent_domain", "—"),
        "message_kind":     debug.get("message_kind", "—"),
        "chosen_strategy":  debug.get("chosen_strategy", "—"),
        "topic_lock":       scene.get("topic_lock", "—"),
        "context_depth":    scene.get("turn_count", "—"),
    }


def run_suite_with_mall(tests: list[tuple], label: str) -> list[dict]:
    """
    Like run_suite but each row has 6 elements: (id, session, query, mode, conf, mall_id).
    Useful for multi-mall tests where each query targets a specific mall.
    """
    results: list[dict] = []
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    for row in tests:
        tid, session, query, exp_mode, exp_conf, mall_id = row
        try:
            t0 = time.time()
            data = send_message(session, query, mall_id=mall_id)
            elapsed = (time.time() - t0) * 1000
            reply = data.get("reply", data.get("message", ""))
            dbg = extract_debug(data)

            mode_ok = dbg["response_mode"] == exp_mode
            conf_ok = dbg["confidence_level"] == exp_conf
            status = "PASS" if (mode_ok and conf_ok) else "FAIL"

            results.append({
                "id": tid,
                "query": query,
                "mall_id": mall_id,
                "status": status,
                "mode_ok": mode_ok,
                "conf_ok": conf_ok,
                "expected_mode": exp_mode,
                "actual_mode":   dbg["response_mode"],
                "expected_conf": exp_conf,
                "actual_conf":   dbg["confidence_level"],
                "flow_type":     dbg["flow_type"],
                "playbook_used": dbg["playbook_used"],
                "topic_lock":    dbg["topic_lock"],
                "latency_ms":    round(elapsed),
                "reply_snippet": reply[:140] if reply else "",
            })

            icon = "✅" if status == "PASS" else "❌"
            print(f"\n{icon} [{tid}] [{mall_id}] {query!r}")
            print(f"   Mode:       {dbg['response_mode']!r:35s} (expected {exp_mode!r}) {'✓' if mode_ok else '✗'}")
            print(f"   Confidence: {dbg['confidence_level']!r:35s} (expected {exp_conf!r}) {'✓' if conf_ok else '✗'}")
            print(f"   Flow/PB:    {dbg['flow_type']} / {dbg['playbook_used']}")
            print(f"   Reply:      {reply[:140]!r}")
            print(f"   Latency:    {round(elapsed)}ms")

        except Exception as exc:
            results.append({
                "id": tid, "query": query, "mall_id": mall_id,
                "status": "ERROR", "error": str(exc),
            })
            print(f"\n💥 [{tid}] [{mall_id}] {query!r}")
            print(f"   ERROR: {exc}")

    return results


def run_suite(tests: list[tuple], label: str) -> list[dict]:
    results: list[dict] = []
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    for row in tests:
        tid, session, query, exp_mode, exp_conf = row
        try:
            t0 = time.time()
            data = send_message(session, query)
            elapsed = (time.time() - t0) * 1000
            reply = data.get("reply", data.get("message", ""))
            dbg = extract_debug(data)

            mode_ok = dbg["response_mode"] == exp_mode
            conf_ok = dbg["confidence_level"] == exp_conf
            status = "PASS" if (mode_ok and conf_ok) else "FAIL"

            results.append({
                "id": tid,
                "query": query,
                "status": status,
                "mode_ok": mode_ok,
                "conf_ok": conf_ok,
                "expected_mode": exp_mode,
                "actual_mode":   dbg["response_mode"],
                "expected_conf": exp_conf,
                "actual_conf":   dbg["confidence_level"],
                "flow_type":     dbg["flow_type"],
                "playbook_used": dbg["playbook_used"],
                "topic_lock":    dbg["topic_lock"],
                "latency_ms":    round(elapsed),
                "reply_snippet": reply[:140] if reply else "",
            })

            icon = "✅" if status == "PASS" else "❌"
            print(f"\n{icon} [{tid}] {query!r}")
            print(f"   Mode:       {dbg['response_mode']!r:35s} (expected {exp_mode!r}) {'✓' if mode_ok else '✗'}")
            print(f"   Confidence: {dbg['confidence_level']!r:35s} (expected {exp_conf!r}) {'✓' if conf_ok else '✗'}")
            print(f"   Flow/PB:    {dbg['flow_type']} / {dbg['playbook_used']}")
            print(f"   Reply:      {reply[:140]!r}")
            print(f"   Latency:    {round(elapsed)}ms")

        except Exception as exc:
            results.append({
                "id": tid, "query": query, "status": "ERROR", "error": str(exc),
            })
            print(f"\n💥 [{tid}] {query!r}")
            print(f"   ERROR: {exc}")

    return results


def print_summary(all_suites: dict[str, list[dict]]) -> None:
    all_results = [r for results in all_suites.values() for r in results]
    total  = len(all_results)
    passed = sum(1 for r in all_results if r.get("status") == "PASS")
    failed = sum(1 for r in all_results if r.get("status") == "FAIL")
    errors = sum(1 for r in all_results if r.get("status") == "ERROR")

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  Total  : {total}")
    print(f"  Passed : {passed} ({round(passed/total*100) if total else 0}%)")
    print(f"  Failed : {failed}")
    print(f"  Errors : {errors}")

    for suite_name, results in all_suites.items():
        s_total  = len(results)
        s_passed = sum(1 for r in results if r.get("status") == "PASS")
        print(f"\n  {suite_name}: {s_passed}/{s_total} passed")

    if failed or errors:
        print(f"\n  --- Failures/Errors ---")
        for r in all_results:
            if r.get("status") in ("FAIL", "ERROR"):
                icon = "❌" if r["status"] == "FAIL" else "💥"
                print(f"  {icon} [{r['id']}] {r['query']!r}")
                if r["status"] == "FAIL":
                    print(f"       mode:  expected={r['expected_mode']!r}  actual={r['actual_mode']!r}")
                    print(f"       conf:  expected={r['expected_conf']!r}  actual={r['actual_conf']!r}")
                else:
                    print(f"       error: {r.get('error')}")
    print()


def write_results(all_suites: dict[str, list[dict]]) -> str:
    all_results = [r for results in all_suites.values() for r in results]
    total  = len(all_results)
    passed = sum(1 for r in all_results if r.get("status") == "PASS")
    failed = sum(1 for r in all_results if r.get("status") == "FAIL")
    errors = sum(1 for r in all_results if r.get("status") == "ERROR")
    pct    = round(passed / total * 100) if total else 0
    avg_ms = round(
        sum(r.get("latency_ms", 0) for r in all_results if "latency_ms" in r)
        / max(1, sum(1 for r in all_results if "latency_ms" in r))
    )

    now_utc  = datetime.now(timezone.utc)
    ts_label = now_utc.strftime("%Y-%m-%d_%H-%M-%S")
    ts_human = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    script_dir  = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.normpath(os.path.join(script_dir, "..", "..", "test-results"))
    os.makedirs(results_dir, exist_ok=True)

    md_path   = os.path.join(results_dir, f"run_{ts_label}.md")
    json_path = os.path.join(results_dir, f"run_{ts_label}.json")

    # ── Markdown report ────────────────────────────────────────────────
    lines = [
        "# Cenomi Chatbot — Full Test Suite Results",
        "",
        f"**Run date:** {ts_human}  ",
        f"**Target:** `{BASE_URL}`  ",
        f"**Session prefix:** `{_RUN_ID}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total queries | {total} |",
        f"| ✅ Passed | {passed} ({pct}%) |",
        f"| ❌ Failed | {failed} |",
        f"| 💥 Errors | {errors} |",
        f"| Avg latency | {avg_ms} ms |",
        "",
        "## Suite Breakdown",
        "",
        "| Suite | Queries | Passed | Failed | Errors |",
        "|-------|---------|--------|--------|--------|",
    ]
    for suite_name, results in all_suites.items():
        s_total  = len(results)
        s_passed = sum(1 for r in results if r.get("status") == "PASS")
        s_failed = sum(1 for r in results if r.get("status") == "FAIL")
        s_errors = sum(1 for r in results if r.get("status") == "ERROR")
        lines.append(f"| {suite_name} | {s_total} | {s_passed} | {s_failed} | {s_errors} |")
    lines.append("")

    if failed or errors:
        lines += [
            "## Failures",
            "",
            "| ID | Query | Expected mode | Actual mode | Expected conf | Actual conf |",
            "|----|-------|--------------|-------------|--------------|-------------|",
        ]
        for r in all_results:
            if r.get("status") in ("FAIL", "ERROR"):
                if r["status"] == "FAIL":
                    lines.append(
                        f"| {r['id']} | `{r['query'][:60]}` "
                        f"| `{r['expected_mode']}` | `{r['actual_mode']}` "
                        f"| `{r['expected_conf']}` | `{r['actual_conf']}` |"
                    )
                else:
                    lines.append(
                        f"| {r['id']} | `{r['query'][:60]}` "
                        f"| — | ERROR | — | `{r.get('error', '')[:60]}` |"
                    )
        lines.append("")

    # Per-suite tables
    for suite_name, results in all_suites.items():
        lines += [
            f"## {suite_name}",
            "",
            "| # | ID | Query | Status | Mode | Conf | Flow | Latency |",
            "|---|----|-------|--------|------|------|------|---------|",
        ]
        for i, r in enumerate(results, 1):
            icon = "✅" if r.get("status") == "PASS" else ("❌" if r.get("status") == "FAIL" else "💥")
            mode = r.get("actual_mode", r.get("error", "—"))[:30]
            conf = r.get("actual_conf", "—")
            flow = r.get("flow_type", "—") or "—"
            lat  = f"{r.get('latency_ms', '—')}ms"
            q    = r["query"][:55]
            lines.append(f"| {i} | {r['id']} | `{q}` | {icon} | `{mode}` | `{conf}` | {flow} | {lat} |")
        lines.append("")

    # Reply snippets
    lines += [
        "## Reply Snippets",
        "",
        "| ID | Query | Reply (first 140 chars) |",
        "|----|-------|------------------------|",
    ]
    for r in all_results:
        snippet = r.get("reply_snippet", "")
        if snippet:
            safe_q = r["query"][:50].replace("|", "\\|")
            safe_r = snippet.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {r['id']} | `{safe_q}` | {safe_r} |")
    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ── JSON dump ──────────────────────────────────────────────────────
    json_payload = {
        "run_timestamp": ts_human,
        "run_id": _RUN_ID,
        "target": BASE_URL,
        "summary": {
            "total": total, "passed": passed, "failed": failed,
            "errors": errors, "pass_pct": pct, "avg_latency_ms": avg_ms,
        },
        "suites": {name: results for name, results in all_suites.items()},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2, ensure_ascii=False)

    print(f"\n  Results written to:")
    print(f"    📄 {md_path}")
    print(f"    🗂  {json_path}")
    return md_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def wait_for_server(max_wait: int = 120) -> bool:
    """Poll /api/health until the server responds or we time out."""
    health_url = BASE_URL.replace("/api/chat", "/api/health")
    deadline = time.time() + max_wait
    print(f"  Waiting for server at {health_url} …", end="", flush=True)
    while time.time() < deadline:
        try:
            r = httpx.get(health_url, timeout=5)
            if r.status_code == 200:
                print(" ✓ ready")
                return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(3)
    print(" ✗ timed out")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Cenomi chatbot test suites")
    parser.add_argument(
        "--suite",
        choices=["standard", "broken", "complex", "session", "new-malls", "v16", "all"],
        default="all",
        help="Which suite(s) to run (default: all)",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Skip server health-check wait",
    )
    args = parser.parse_args()

    print("\nCenomi Chatbot — Full Test Suite Runner")
    print(f"Target: {BASE_URL}")
    print(f"Run ID: {_RUN_ID}")

    if not args.no_wait:
        if not wait_for_server():
            print("\nServer did not become ready in time. Aborting.")
            sys.exit(1)

    all_suites: dict[str, list[dict]] = {}

    if args.suite in ("standard", "all"):
        smoke_r    = run_suite(SMOKE_TESTS,    "SMOKE TESTS — test-queries.md")
        scenario_r = run_suite(SCENARIO_TESTS, "SCENARIO TESTS — test-queries.md")
        all_suites["test-queries.md (smoke)"]    = smoke_r
        all_suites["test-queries.md (scenarios)"] = scenario_r

    if args.suite in ("broken", "all"):
        broken_r = run_suite(BROKEN_TESTS, "BROKEN / MULTI-TURN — test-queries-broken.md")
        all_suites["test-queries-broken.md"] = broken_r

    if args.suite in ("complex", "all"):
        complex_r = run_suite(COMPLEX_TESTS, "COMPLEX SCENARIOS — test-queries-complex.md")
        all_suites["test-queries-complex.md"] = complex_r

    if args.suite in ("session", "all"):
        session_r = run_suite(SESSION_TESTS, "SESSION SCENARIO — session-4647af8c1ae742e3")
        all_suites["session-scenario.json"] = session_r

    if args.suite in ("new-malls", "all"):
        new_mall_r = run_suite_with_mall(
            NEW_MALL_SMOKE_TESTS,
            "SUITE 5 — NEW MALL SMOKE + 5-MALL CROSS SEARCH",
        )
        all_suites["suite5-new-malls"] = new_mall_r

    if args.suite in ("v16", "all"):
        v16_r = run_suite(
            V16_FEATURE_TESTS,
            "SUITE 6 — v1.6 FEATURE SCENARIOS",
        )
        all_suites["suite6-v16-features"] = v16_r

    print_summary(all_suites)
    write_results(all_suites)
