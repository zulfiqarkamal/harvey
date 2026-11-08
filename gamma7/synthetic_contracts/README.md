# Synthetic ICT Contracts — Gamma7 MVP Test Dataset

5 fictional contracts for testing the DORA Art. 30 / GDPR Art. 28 analysis engine.
All companies, names, and contract numbers are entirely fictional.
No resemblance to real entities is intended.

Each contract has deliberately inserted gaps — the expected findings are documented
in `expected_findings.yaml` for automated validation.

## Contract Summary

| File | Supplier | Function | Classification | Key gaps |
|------|----------|----------|----------------|----------|
| `contract_01_cloudhost.md` | NordCloud Hosting A/S | Core banking infrastructure (cloud) | Critical/Important | Missing TLPT clause, no data return format, vague SLA metrics |
| `contract_02_payment.md` | PayLink Technologies Ltd | Payment processing | Critical/Important | No sub-contracting clause, audit right limited to certifications only, no incident notification timeframe |
| `contract_03_legacy.md` | DataCenter DK ApS | Legacy mainframe housing | Non-critical | Pre-DORA contract — multiple missing clauses, no GDPR DPA at all |
| `contract_04_saas.md` | ComplianceTools International BV | GRC SaaS platform | Non-critical | No data location specified, SCCs not referenced despite non-EEA processing, no BCP testing obligation |
| `contract_05_good.md` | SecureVault Nordic A/S | Document management & archiving | Critical/Important | Largely compliant — 1 minor ambiguity (audit notice period 45 days) |
