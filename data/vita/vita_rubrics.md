# WanderWheels VITA — Call-Quality Rubrics
## Sample Data for `vita-rubrics` Azure AI Search Index

> **Purpose:** These rubrics are used by the `requestScore` tool to evaluate a
> learner's responses during practice scenarios. Load each rubric section into
> the `vita-rubrics` Azure AI Search index. The `rubricId` field must match
> the identifier used in `startPractice` and `requestScore` calls.
>
> **Pass threshold:** score ≥ 0.70 (70 %) is considered a pass. Below 0.70,
> the learner repeats the section with targeted coaching on the weak areas.

---

## Rubric: onroad-greeting-v1

**Applies to:** `startPractice(scenario='customer-greeting-01')`
**Roles:** driver, guide, coordinator
**Countries:** global baseline

### Criteria

| # | Criterion | Weight | Pass condition |
|---|---|---|---|
| 1 | Warm welcome | 20 % | Learner greets the guest by name or with a friendly opener within the first sentence. |
| 2 | Role introduction | 20 % | Learner clearly states their name and role ("Hi, I'm Alex, your WanderWheels driver today"). |
| 3 | Journey overview | 25 % | Learner briefly outlines the day's journey or first activity without reading from a script. |
| 4 | Guest comfort check | 20 % | Learner asks if the guest has any immediate needs (water, comfort stop, accessibility). |
| 5 | Safety briefing mention | 15 % | Learner mentions that a safety briefing will follow without being prompted. |

### Scoring guide

- **5 / Excellent (0.90–1.00):** All criteria met; tone is natural and confident.
- **4 / Good (0.80–0.89):** Four of five criteria met; minor hesitation only.
- **3 / Satisfactory (0.70–0.79):** Three criteria met; greeting is functional but lacks warmth or a key element.
- **2 / Needs work (0.50–0.69):** Two criteria met; guest may feel unwelcome or confused.
- **1 / Unsatisfactory (0.00–0.49):** Fewer than two criteria met; redo required.

### Example strengths

- "Great energy — the guest feels welcomed immediately."
- "Role and name intro was clear and professional."

### Example improvements

- "Ask about accessibility needs before launching into the itinerary."
- "Mention the safety briefing proactively — don't wait to be asked."

---

## Rubric: onroad-safety-v1

**Applies to:** `startPractice(scenario='vehicle-safety-01')`
**Roles:** driver, guide
**Countries:** global baseline

### Criteria

| # | Criterion | Weight | Pass condition |
|---|---|---|---|
| 1 | Emergency exits | 25 % | Learner correctly identifies all emergency exits and how to open them. |
| 2 | Seatbelt policy | 20 % | Learner states the mandatory seatbelt policy and checks that all guests comply before departure. |
| 3 | Speed and road rules | 20 % | Learner correctly states the applicable speed limit and key road rules for the operating country. |
| 4 | Emergency contact | 20 % | Learner correctly states the 24/7 operations centre as the first emergency contact. |
| 5 | Vehicle breakdown | 15 % | Learner correctly describes the breakdown procedure (pull over safely, hazard lights, call ops centre). |

### Scoring guide

- **5 / Excellent (0.90–1.00):** All five criteria met; delivery is confident and clear.
- **4 / Good (0.80–0.89):** Four criteria met; one minor omission.
- **3 / Satisfactory (0.70–0.79):** Three criteria met; safety fundamentals present.
- **2 / Needs work (0.50–0.69):** Two criteria met; significant safety gaps present.
- **1 / Unsatisfactory (0.00–0.49):** Critical safety information missing; redo required.

### Example strengths

- "Emergency exit identification was fast and accurate."
- "Seatbelt check included all guests before departure."

### Example improvements

- "Always state the 24/7 ops centre number — not just 'call your manager'."
- "Describe pulling over to the shoulder before activating hazard lights."

---

## Rubric: onroad-complaint-v1

**Applies to:** `startPractice(scenario='complaint-handling-01')`
**Roles:** driver, guide, coordinator
**Countries:** global baseline

### Criteria

| # | Criterion | Weight | Pass condition |
|---|---|---|---|
| 1 | Acknowledge and empathise | 25 % | Learner acknowledges the guest's complaint and expresses genuine empathy without being defensive. |
| 2 | Clarify the issue | 20 % | Learner asks a clarifying question to fully understand the guest's concern before responding. |
| 3 | Offer a concrete next step | 25 % | Learner offers a specific, actionable resolution or escalation path (not just "I'll look into it"). |
| 4 | Follow escalation policy | 20 % | Learner correctly identifies when to escalate to the coordinator or ops centre. |
| 5 | Close the loop | 10 % | Learner confirms the guest's understanding of the next step and checks they feel heard. |

### Scoring guide

- **5 / Excellent (0.90–1.00):** All criteria met; guest feels heard and has a clear resolution path.
- **4 / Good (0.80–0.89):** Four criteria met; resolution is clear but one element is thin.
- **3 / Satisfactory (0.70–0.79):** Three criteria met; complaint is handled but empathy or next step is weak.
- **2 / Needs work (0.50–0.69):** Two criteria met; guest may remain frustrated or confused.
- **1 / Unsatisfactory (0.00–0.49):** Complaint is dismissed, deflected, or escalation is incorrect.

### Example strengths

- "Strong empathy in the opening — the guest immediately felt heard."
- "Concrete next step (ops centre call within 30 minutes) was specific and reassuring."

### Example improvements

- "Ask one clarifying question before jumping to a solution."
- "Close the loop — confirm the guest understands what happens next."

---

## Rubric: onroad-guestexperience-v1

**Applies to:** `startPractice(scenario='guest-experience-01')`
**Roles:** guide, coordinator
**Countries:** global baseline

### Criteria

| # | Criterion | Weight | Pass condition |
|---|---|---|---|
| 1 | Local knowledge | 30 % | Guide shares accurate local context (history, culture, nature) relevant to the current location. |
| 2 | Guest engagement | 25 % | Guide invites participation (questions, photos, interaction) rather than delivering a monologue. |
| 3 | Pacing and clarity | 20 % | Commentary is delivered at a pace guests can follow; no jargon without explanation. |
| 4 | Dietary / accessibility awareness | 15 % | Guide proactively checks or references any dietary or mobility needs before the next stop. |
| 5 | WanderWheels brand alignment | 10 % | Commentary reflects the WanderWheels guest promise ("safe, seamless, and surprising"). |

### Scoring guide

- **5 / Excellent (0.90–1.00):** Guests are engaged, informed, and delighted.
- **4 / Good (0.80–0.89):** Four criteria met; commentary is engaging but one element is thin.
- **3 / Satisfactory (0.70–0.79):** Three criteria met; basic guest experience delivered.
- **2 / Needs work (0.50–0.69):** Two criteria met; guests may feel uninformed or passive.
- **1 / Unsatisfactory (0.00–0.49):** Commentary is inaccurate, disengaging, or misaligned with brand.

### Example strengths

- "Excellent local history detail — guests were visibly engaged."
- "Proactively asked about dietary needs before the vineyard stop."

### Example improvements

- "Invite guests to ask questions rather than delivering a lecture."
- "Reference the WanderWheels promise when linking experiences together."
