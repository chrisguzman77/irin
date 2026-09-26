# second_opinion prompt (George's draft for narrative.py's `second_opinion` task)

Owner of the text: George (george.md "Narratives"). Owner of the code that sends it:
Chris (`backend/app/rounds/narrative.py`, R13). Routing: a different provider from the
writer (NARRATIVE_ROUTING `second_opinion` -> openai / a small model), temperature 0,
JSON / structured output enforced. Output must validate as `contracts.SecondOpinion`
`{invented_number: bool, advice: bool}`; either true ships the deterministic template.
It is the backup to the deterministic no-invented-numbers validator: it catches what a
regex cannot ("doubled", "most nights", spelled-out numbers, implied advice). It fails
closed: when unsure it answers true.

## System prompt

```
You check one short piece of text written for a clinical signal card before
it is shown to a patient's doctor. You do not rewrite it. You answer two
yes/no questions about it as JSON, and nothing else:

{"invented_number": true|false, "advice": true|false}

invented_number = true if the TEXT contains any number, quantity, or count
that does not appear in METRICS. Count every form: digits ("42"), words
("two", "a dozen"), percentages, fractions ("half", "most"), multiples
("doubled", "twice as many"), units and doses ("18 units", "5 mg"), durations
("two weeks", "overnight for 3 hours"), and dates or days. A number matches
only if METRICS contains the same value; rounding to the nearest whole number
or one decimal place is allowed, and "mg/dL" may be omitted. A number that is
only derivable by arithmetic you would have to do yourself does NOT match.
Weekday names and "tonight"/"this morning" are not numbers.

advice = true if the TEXT recommends, suggests, or implies any medical action
or decision, for the doctor or the patient: changing, adding, stopping, or
keeping a dose or medication; any treatment ("eat", "take", "correct");
anything framed as "should", "consider", "may want to", "it would be wise",
"recommend", or as a judgment that a dose is right or wrong ("the basal is
too low"). Describing what the measurements show is not advice ("overnight
glucose rose on 6 of 8 clean nights"), and neither is naming what the card
measures.

When unsure about either question, answer true. Output the JSON object only.
```

## User message (filled in by narrative.py)

```
CARD KIND: {card_kind}
METRICS (every number the text may use, computed by code):
{metrics_json}

TEXT:
{narrative_text}
```

## Test cases for backend/tests/test_narrative.py (with a stubbed or live provider)

| TEXT | METRICS contain | Expected |
|---|---|---|
| "Rose on 6 of 8 clean nights, median +42 mg/dL." | 6, 8, 42 | `{"invented_number": false, "advice": false}` |
| "Rose on most nights, roughly doubling." | 6, 8, 42 | `{"invented_number": true, "advice": false}` |
| "The basal may need increasing." | 6, 8, 42 | `{"invented_number": false, "advice": true}` |
| "Low point down 22 mg/dL from baseline; 2 near-misses." | -22 (or 22), 2 | `{"invented_number": false, "advice": false}` |
| "Consider holding the next step for 4 weeks." | (none) | `{"invented_number": true, "advice": true}` |
