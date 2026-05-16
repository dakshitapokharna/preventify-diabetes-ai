# ADA/ADCES DSMES 2022 — Team Reference Document

**File:** `ADA_ADCES_DSMES_National_Standards_2022.pdf`  
**Full name:** 2022 National Standards for Diabetes Self-Management Education and Support  
**Issued by:** American Diabetes Association (ADA) + Association of Diabetes Care & Education Specialists (ADCES)

---

## DO NOT INGEST INTO ANY VECTOR STORE NAMESPACE

This is not a knowledge source for the bot. Do not include it in the `clinical` or `compliance` RAG pipeline.

---

## Who should read this

| Role | Why |
|------|-----|
| Conversational designer / product | Understanding what topics a diabetes educator must cover and when |
| Backend engineer | Shaping the bot's conversation flow, topic sequencing, and session structure |
| Clinical lead | Verifying that the AI's scope matches recognised DSMES standards |

## What it covers

- **7 DSMES topic areas** the bot must be able to address:
  1. Healthy eating
  2. Being active
  3. Monitoring (blood glucose, BP, foot checks)
  4. Taking medication
  5. Problem solving
  6. Reducing risks
  7. Psychosocial care and coping

- **4 critical time points** when education must be delivered:
  1. At diagnosis
  2. Annually (or when not meeting targets)
  3. When a complication develops
  4. When transitions in care occur (hospitalisation, care team change)

- Evidence base for why structured education improves HbA1c outcomes
- Definition of "qualified diabetes educator" scope — directly maps to what this AI can and cannot do

## How to use during build

Read this before designing:
- The onboarding conversation flow (which topics to cover at first session)
- The annual follow-up session structure
- The scope boundary rules embedded in the system prompt (what the bot can teach vs. must escalate)
- The 5-tier risk escalation logic — the "reducing risks" and "problem solving" DSMES domains map directly to Tier 2–4 escalation triggers

Once these design decisions are made and encoded into the system prompt and escalation rules, this document is no longer needed at runtime.
