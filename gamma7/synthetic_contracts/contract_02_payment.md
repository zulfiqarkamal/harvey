# Payment Processing Services Agreement
**Contract reference:** FIN-PP-2022-091  
**Date:** 1 July 2022  
**Parties:**
- **Customer:** Fjord Finans A/S, CVR 23456789, Rådhuspladsen 4, 8000 Aarhus C
- **Supplier:** PayLink Technologies Ltd, registered in Ireland (CRO 654321), 1 Grand Canal Square, Dublin 2

---

## 1. Scope of Services

PayLink Technologies Ltd ("PayLink") shall provide payment processing infrastructure to Fjord Finans A/S ("Fjord Finans") including:
- Card transaction authorisation and clearing
- Fraud detection and prevention services
- Payment scheme connectivity (Visa, Mastercard, Dankort via Nets)
- Settlement reconciliation and reporting

**Sub-contracting:** PayLink may sub-contract any part of its obligations to group companies or approved third parties without prior notice to Fjord Finans. PayLink remains responsible for the performance of sub-contractors.

## 2. Service Levels

PayLink shall maintain transaction processing availability of **99.95% per calendar month**.

**Recovery Time Objective (RTO):** 4 hours for critical payment authorisation systems.

Service level credits apply if availability falls below 99.5% in any month: 10% credit for each 0.5% below threshold, capped at 30% of monthly fees.

SLA reports shall be provided monthly.

## 3. Security and Compliance

**3.1** PayLink maintains PCI DSS Level 1 certification and shall maintain it for the duration of this Agreement.

**3.2** PayLink shall implement appropriate technical and organisational measures to protect transaction data against unauthorised access, disclosure, alteration or destruction.

**3.3 Data locations:** Transaction processing infrastructure is located in PayLink's data centres. Fjord Finans acknowledges that for operational resilience, data may be processed in multiple locations within the European Union.

**3.4** PayLink shall perform annual penetration testing of its payment processing infrastructure.

## 4. Incidents

**4.1** PayLink shall notify Fjord Finans of any service outage affecting transaction processing via the support portal as soon as the incident is confirmed.

**4.2** For security incidents: PayLink shall notify Fjord Finans within **72 hours** of detecting a confirmed breach affecting Fjord Finans' transaction data.

## 5. Audit Rights

**5.1** Fjord Finans may request copies of PayLink's current PCI DSS Report on Compliance (ROC) and any other relevant third-party audit reports.

**5.2** PayLink's internal systems are not available for direct customer audit in order to protect the security of the shared infrastructure serving multiple customers.

## 6. Business Continuity

PayLink shall maintain and test its business continuity and disaster recovery plans in accordance with PCI DSS requirements. Test results are summarised in the annual PCI DSS ROC.

## 7. Regulatory Cooperation

PayLink shall cooperate with Fjord Finans' regulators as required by applicable law.

## 8. Termination

Either party may terminate with 180 days' written notice. Fjord Finans may terminate immediately if PayLink loses its PCI DSS certification and fails to restore it within 60 days.

## 9. Data Processing

**9.1** For the purposes of GDPR, PayLink acts as a data processor with respect to personal data of Fjord Finans' customers included in transaction data.

**9.2** PayLink shall process personal data only as necessary to provide the Services.

**9.3** PayLink shall implement appropriate security measures for personal data.

**9.4** PayLink shall notify Fjord Finans of personal data breaches in accordance with Clause 4.2.

**9.5 Sub-processors:** PayLink uses group companies and approved infrastructure providers as sub-processors. A current list is available on the PayLink compliance portal.

**9.6 International transfers:** Fjord Finans acknowledges that some processing may involve transfers within the EU. PayLink shall ensure that any transfers comply with applicable data protection law.

## 10. Governing Law

This Agreement is governed by Irish law.
