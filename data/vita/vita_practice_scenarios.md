# WanderWheels VITA — Practice Scenarios
## Sample Data for `vita-scenarios` Azure AI Search Index

> **Purpose:** These scripted role-play scenarios are used by the `startPractice`
> tool to launch voice practice sessions. Each scenario includes an opening
> prompt that VITA reads aloud, a role for the learner, coaching hints, and the
> rubric that `requestScore` should apply when the scenario ends.
>
> Load each scenario section into the `vita-scenarios` Azure AI Search index
> (or Cosmos DB container `vita-scenarios`). The `scenarioId` field must match
> the identifier passed to `startPractice`.

---

## Scenario: customer-greeting-01

**Rubric:** `onroad-greeting-v1`
**Roles:** driver, guide, coordinator
**Countries:** global baseline
**Duration:** ~3–5 minutes

### Opening prompt (VITA reads aloud)

> "Let's practise welcoming a guest. Imagine you have just picked up a guest at
> the hotel. They are loading their luggage into the vehicle. I'll play the guest.
> Start when you're ready — go ahead and greet me."

### Learner role

Welcome the guest as if starting a WanderWheels tour day. You should:
1. Greet the guest warmly and introduce yourself by name and role.
2. Give a brief overview of the day's journey.
3. Check the guest has everything they need.
4. Mention that you'll run through a brief safety overview once everyone is seated.

### Coaching hints (surfaced by VITA if the learner pauses or struggles)

- "Try starting with your name and role — guests feel more comfortable when they
  know who is looking after them."
- "Give the guest a sense of the day ahead — even one sentence helps set
  expectations."
- "Ask if the guest needs anything before you set off — water, a comfort stop,
  anything accessibility-related."

### Guest responses (VITA plays the guest)

- Default: "Hi! Yes, ready to go. Really looking forward to today."
- Follow-up if learner skips the journey overview: "What are we doing today?"
- Follow-up if learner skips comfort check: "Actually, is there a bathroom stop
  on the way?"

### Ending condition

VITA calls `endPractice()` after the learner has delivered their greeting and
addressed at least one follow-up question, or after 5 minutes of conversation.

---

## Scenario: vehicle-safety-01

**Rubric:** `onroad-safety-v1`
**Roles:** driver, guide
**Countries:** global baseline
**Duration:** ~4–6 minutes

### Opening prompt (VITA reads aloud)

> "Now let's practise the vehicle safety briefing. All guests are seated and
> buckled up. You're about to depart. Please run through the safety briefing
> as you would in a real departure."

### Learner role

Deliver the full pre-departure safety briefing. You should cover:
1. Emergency exits — location and how to open them.
2. Seatbelt policy — mandatory for all guests at all times.
3. Key road rules for the operating country (speed limits, overtaking).
4. Emergency contact: 24/7 operations centre.
5. Breakdown procedure.

### Coaching hints

- "Cover emergency exits first — guests need to know where they are before
  the vehicle moves."
- "State the seatbelt rule clearly — it's mandatory, not optional."
- "Mention the ops centre number as the primary emergency contact."

### Guest responses (VITA plays a curious guest)

- "What do I do if there's a fire?"
- "Are seatbelts really required? I'm just going a short distance."
- "What happens if the van breaks down on the highway?"

### Ending condition

VITA calls `endPractice()` after the learner has covered all five safety topics
and responded to at least one guest question, or after 6 minutes.

---

## Scenario: complaint-handling-01

**Rubric:** `onroad-complaint-v1`
**Roles:** driver, guide, coordinator
**Countries:** global baseline
**Duration:** ~5–7 minutes

### Opening prompt (VITA reads aloud)

> "Let's practise handling a guest complaint. A guest approaches you mid-tour
> and says they are unhappy with the change to today's itinerary — a planned
> stop was removed without notice. I'll play the guest. Respond as you would
> on the road."

### Guest opening line

> "Excuse me — I was told we'd be stopping at the viewpoint this morning and
> now I hear it's been cancelled. I paid a lot of money for this tour and this
> is really disappointing."

### Learner role

Handle the complaint professionally. You should:
1. Acknowledge the guest's frustration with genuine empathy.
2. Ask a clarifying question to understand the full picture.
3. Explain what happened (or that you need to find out) and offer a concrete
   next step.
4. Escalate to the coordinator or ops centre if the issue is beyond your authority.
5. Confirm the guest understands the next step and check they feel heard.

### Coaching hints

- "Lead with empathy — acknowledge how they feel before explaining anything."
- "Ask a clarifying question: how did they hear about the stop? Was it in the
  printed itinerary?"
- "Offer something concrete — 'I'll check with our coordinator in the next
  10 minutes' is better than 'I'll look into it'."

### Guest responses

- After acknowledgement: "Thank you for listening. But what are you going to
  do about it?"
- After concrete next step: "OK, but can you guarantee the viewpoint is added
  to tomorrow's itinerary?"
- If learner fails to empathise: "You don't seem to care about my experience."

### Ending condition

VITA calls `endPractice()` after the learner has addressed all five criteria
topics or the guest confirms satisfaction, or after 7 minutes.

---

## Scenario: guest-experience-01

**Rubric:** `onroad-guestexperience-v1`
**Roles:** guide, coordinator
**Countries:** global baseline
**Duration:** ~5–8 minutes

### Opening prompt (VITA reads aloud)

> "Let's practise delivering a location commentary. We've just arrived at a
> scenic coastal viewpoint. The guests are stepping off the vehicle and
> gathering around you. Deliver your commentary for this stop."

### Learner role

Deliver an engaging two-to-three-minute commentary for a coastal viewpoint
stop. You should:
1. Share accurate local context (geography, history, wildlife, or culture).
2. Invite guest participation — ask a question or encourage photos.
3. Pace the commentary so everyone can follow.
4. Check dietary or mobility needs before mentioning the next food stop.
5. Link the experience to the WanderWheels promise.

### Coaching hints

- "Open with a hook — a surprising fact or an evocative description."
- "Pause after your hook and ask the guests a question to draw them in."
- "Mention the café at the next stop and ask if anyone has dietary needs before
  you arrive."

### Guest responses

- "Is this the highest point on the tour?"
- "Are there any dolphins here?"
- "My wife is vegetarian — will the next stop have options for her?"

### Ending condition

VITA calls `endPractice()` after the learner has delivered their commentary and
responded to at least two guest questions, or after 8 minutes.

---

## Scenario: emergency-response-01

**Rubric:** `onroad-safety-v1`
**Roles:** driver, guide
**Countries:** global baseline
**Duration:** ~4–5 minutes

### Opening prompt (VITA reads aloud)

> "This is a simulated emergency scenario. While driving on a regional highway,
> a warning light comes on and the vehicle begins to vibrate. There are six
> guests on board. Walk me through exactly what you would do, step by step."

### Learner role

Respond to a vehicle warning / potential breakdown scenario. You should:
1. Pull over to the shoulder safely with hazard lights activated.
2. Calmly inform guests and instruct them to remain seated with seatbelts on.
3. Call the 24/7 operations centre immediately.
4. Assess whether guests should exit the vehicle (only if safe to do so).
5. Follow the ops centre's instructions and keep guests informed.

### Coaching hints

- "State your first action clearly — safe pull-over before anything else."
- "Communicate calmly to guests — panic is contagious."
- "Call the ops centre before attempting any vehicle diagnosis yourself."

### Guest responses

- "What's happening? Should we get out?"
- "Can't you just keep driving to the next town?"
- "How long will we be stuck here?"

### Ending condition

VITA calls `endPractice()` after the learner has covered all five steps and
responded to at least one guest question, or after 5 minutes.
