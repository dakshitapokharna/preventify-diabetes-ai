# DPDP Rules 2025 — Team Reference Document

**File:** `DPDP_Rules_2025_Nov_Finalized.pdf`  
**Full name:** Digital Personal Data Protection Rules 2025 (GSR 846(E), November 2025)  
**Issued by:** Ministry of Electronics and Information Technology (MeitY), Government of India

---

## DO NOT INGEST INTO ANY VECTOR STORE NAMESPACE

This is not a knowledge source for the bot. Do not include it in the `clinical` or `compliance` RAG pipeline.

---

## Who should read this

| Role | Why |
|------|-----|
| Backend engineer | Designing the consent screen, data deletion API, and conversation log retention |
| Compliance lead | Preparing Data Fiduciary documentation and breach response SOP (Blocker B5) |
| Legal / operations | Understanding Preventify's obligations as a Data Fiduciary under DPDP 2023 |

## What it covers

- How a Data Fiduciary (Preventify) must collect and store personal data
- Consent requirements — must be in the user's language, plain words, no buried legalese
- Data deletion rights — a patient can request full erasure at any time
- Breach protocol — notification to MeitY required; timeline and format specified
- Special rules for sensitive personal data (health data is explicitly classified as sensitive)
- Cross-border data transfer restrictions

## Enforcement timeline

Phased enforcement begins 12–18 months from November 2025 — approximately mid-to-late 2026, which aligns with the product go-live window. Do not treat this as a future concern; design must be compliant from day one.

## Linked blocker

This document is primary input to **Blocker B5 — SaMD Regulatory Pathway** (see CLAUDE.md). Specifically items B5.4 (DPDP data architecture), B5.5 (Data Fiduciary documentation), and B5.6 (conversation logging compliance).
