# WanderWheels On-Road Standard Operating Procedures
## VITA Training Content — Sample Data

> **Purpose:** This file is the seed dataset for the `vita-onroad-sop` Azure AI
> Search index. It covers the core on-road curriculum used by VITA to ground
> training responses. Load this file (or its sections) into the search index
> using the Azure AI Search import wizard or the provided ingestion script.
>
> **Scope:** Roles: `driver`, `guide`, `coordinator`. Countries: global baseline
> with country-specific addendums noted inline.

---

## Section: onboarding-01 — Welcome and Role Overview

### Learning objectives
- Understand your on-road role within the WanderWheels operation.
- Know the chain of command and who to contact in an emergency.
- Be able to describe the WanderWheels guest promise in one sentence.

### Content

Welcome to WanderWheels. As an on-road team member you are the face of the
company for every guest you encounter. Your actions directly shape the guest
experience and the WanderWheels reputation.

**Chain of command**
1. On-road coordinator (your first point of contact on shift)
2. Regional operations manager
3. 24/7 operations centre (for safety or security emergencies)

**The WanderWheels guest promise**
"Safe, seamless, and surprising — every journey, every time."

Every decision you make on road should be tested against this promise. If an
action does not keep guests safe, keep the journey seamless, or add a moment of
delight, reconsider it and escalate if unsure.

**Key contacts**
- Operations centre (24/7): contact your regional operations manager for the
  current number — do not share this externally.
- Emergency services: always call local emergency services first, then notify
  the operations centre.

### Practice scenarios
- `customer-greeting-01`

### Rubric
- `onroad-greeting-v1`

---

## Section: safety-01 — Vehicle Safety Checks

### Learning objectives
- Complete a pre-departure vehicle safety check independently.
- Identify defects that prevent departure and defects that must be reported.
- Document and escalate a defect correctly.

### Content

A thorough pre-departure check protects guests, other road users, and your
operating licence. It must be completed before every trip, without exception.

**Pre-departure checklist (all vehicles)**
1. Tyres — check pressure and visible condition on all four wheels (and spare).
2. Lights — headlights, indicators, brake lights, hazard lights.
3. Fluids — engine oil, coolant, windscreen washer.
4. Mirrors — adjust all mirrors before boarding guests.
5. Seatbelts — confirm every seatbelt retracts and latches.
6. Emergency equipment — first-aid kit present and in date, fire extinguisher
   charged, high-visibility vests available for every seat.
7. Cleanliness — cabin clean and luggage secure.

**Stop-departure defects (do not move the vehicle)**
- Any tyre with visible damage, sidewall bulge, or pressure below minimum.
- Any non-functioning brake light.
- Missing or expired emergency equipment.

**Report-and-proceed defects (report, then depart if safe)**
- Windscreen chip outside the driver's direct line of sight.
- Non-critical interior light failure.

**Reporting process**
1. Photograph the defect with the WanderWheels fleet app.
2. Submit a defect report before departure.
3. Notify your coordinator verbally.

### Practice scenarios
- `safety-check-01`

### Rubric
- `onroad-safety-v1`

---

## Section: safety-02 — Emergency Procedures

### Learning objectives
- Follow the correct sequence of actions in a road traffic incident.
- Manage guest welfare during an emergency.
- Complete a post-incident report accurately.

### Content

In any emergency, the priority order is:
1. **Life safety** — prevent further injury to guests, crew, and other road users.
2. **Call emergency services** — dial the local emergency number immediately.
3. **Notify operations** — call the operations centre to activate the emergency protocol.
4. **Preserve the scene** — do not move vehicles unless there is an immediate danger.
5. **Support guests** — keep guests calm, account for all passengers, provide
   first aid if trained to do so.
6. **Document** — record time, location, circumstances, and witness details.

**Guest communication during an emergency**
- Speak clearly and calmly: "We have had an incident. I am making sure everyone
  is safe. Please stay in your seat / move to the assembly point."
- Do not speculate about cause, blame, or insurance.
- Do not post on social media.

**Country-specific addendums**
- **AE (UAE):** Do not move any vehicle involved in a collision — wait for police.
  Exchanging insurance details without a police report is insufficient.
- **GB (UK):** Duty to stop and exchange details applies immediately; call 999
  for injuries, 101 for non-injury incidents.

### Rubric
- `onroad-safety-v1`

---

## Section: guest-experience-01 — Customer Greeting Standards

### Learning objectives
- Deliver a consistent, warm greeting that meets WanderWheels standards.
- Adapt greeting style to cultural context.
- Handle a guest complaint in the first 60 seconds.

### Content

**Standard greeting sequence**
1. Make eye contact and smile before speaking.
2. Greet by time of day: "Good morning / afternoon / evening."
3. Introduce yourself: "My name is [Name], your WanderWheels [role] today."
4. Confirm the booking: "I have you travelling to [destination] — is that correct?"
5. Assist with boarding: offer help with luggage, direct guests to their seats.

**Cultural adaptations**
- In formal cultures (e.g. Japan, UAE, Germany), avoid first-name use unless
  invited; use "Mr / Ms [Surname]."
- In informal cultures (e.g. Australia, USA), a first-name greeting after the
  initial formal opener is usually appreciated.
- Religious greetings (e.g. "As-salamu alaykum"): respond in kind only if you
  are comfortable doing so; never mimic insincerely.

**Handling an opening complaint**
1. Listen without interrupting.
2. Acknowledge: "Thank you for telling me — I can see that's frustrating."
3. Act or escalate: fix what you can immediately; call the coordinator for
   anything outside your authority.
4. Follow up: before the journey ends, check back with the guest.

### Practice scenarios
- `customer-greeting-01`
- `complaint-handling-03`

### Rubric
- `onroad-greeting-v1`

---

## Section: guest-experience-02 — Complaint Handling

### Learning objectives
- De-escalate an upset guest using the four-step model.
- Know what you can resolve on-road vs what needs coordinator escalation.
- Document a complaint correctly.

### Content

**Four-step complaint model (HEAR)**
1. **H — Hear:** Give the guest your full attention. Do not interrupt.
2. **E — Empathise:** Name the emotion: "I understand you're disappointed."
3. **A — Act:** State what you will do: "I will speak to the coordinator now."
4. **R — Resolve:** Confirm the outcome and thank the guest for raising it.

**On-road authority (what you can resolve yourself)**
- Seat changes within the vehicle.
- Adjusting in-vehicle temperature or music.
- Providing bottled water or snack (where available).
- Apologising on behalf of WanderWheels for a service failure.

**Escalate to coordinator**
- Refund requests.
- Complaints about another staff member.
- Requests to change the route or destination.
- Any complaint involving injury, discrimination, or harassment.

**Documentation**
Log every complaint in the WanderWheels fleet app under "Guest Feedback" before
the end of your shift, even if resolved. Include: time, guest name (if given),
description, action taken, outcome.

### Practice scenarios
- `complaint-handling-03`

### Rubric
- `onroad-complaint-v1`

---

## Rubric: onroad-greeting-v1

**Purpose:** Score a learner's customer-greeting practice against WanderWheels
standards.

| Criterion | Weight | Pass threshold |
|---|---|---|
| Eye contact / opening acknowledgement | 20% | Demonstrated |
| Correct greeting formula (time of day + name + role) | 30% | All three elements present |
| Booking confirmation | 25% | Destination confirmed with guest |
| Cultural appropriateness | 15% | No inappropriate adaptation |
| Offer of boarding assistance | 10% | Offered unprompted |

**Pass score:** ≥ 0.70  
**Fail score:** < 0.70 → repeat `guest-experience-01` before advancing.

---

## Rubric: onroad-safety-v1

**Purpose:** Score a learner's safety-check or emergency-response practice.

| Criterion | Weight | Pass threshold |
|---|---|---|
| Correct priority order (life safety first) | 30% | Stated correctly |
| Emergency services called first | 25% | Yes |
| Operations notified second | 20% | Yes |
| Guest communication (calm, accurate) | 15% | Calm tone, no speculation |
| Documentation / reporting intent stated | 10% | Mentioned |

**Pass score:** ≥ 0.75  
**Fail score:** < 0.75 → repeat safety section before advancing.

---

## Rubric: onroad-complaint-v1

**Purpose:** Score a learner's complaint-handling practice using the HEAR model.

| Criterion | Weight | Pass threshold |
|---|---|---|
| Heard without interrupting | 20% | No interruptions |
| Empathy statement given | 25% | Emotion named or acknowledged |
| Action stated clearly | 30% | Specific action communicated |
| Resolution confirmed and guest thanked | 15% | Confirmed |
| Escalation knowledge (knew when to escalate) | 10% | Correct boundary stated |

**Pass score:** ≥ 0.70  
**Fail score:** < 0.70 → repeat `guest-experience-02` before advancing.
