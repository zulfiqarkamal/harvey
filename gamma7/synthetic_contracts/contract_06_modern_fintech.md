# ICT Services Agreement
## NordicPay Solutions A/S and CloudCore Infrastructure ApS

**Contract Date:** 15 January 2024
**Effective Date:** 1 February 2024
**Reference:** NPS-ICT-2024-017

---

### PARTIES

**Financial Entity:** NordicPay Solutions A/S, CVR 34567891, Havnegade 22, 1058 Copenhagen K, Denmark ("Client")  
**ICT Third-Party Service Provider:** CloudCore Infrastructure ApS, CVR 45678902, Lyngby Allé 2, 2800 Kongens Lyngby, Denmark ("Provider")

Client is a payment institution supervised by the Danish Financial Supervisory Authority (Finanstilsynet) and is subject to DORA (Regulation (EU) 2022/2554).

---

## ARTICLE 1 — SCOPE AND SERVICES

### 1.1 Service Description

Provider shall deliver the following ICT services to Client:

- **Core Payment Processing Platform:** Real-time transaction processing for card payments, instant transfers, and batch settlement
- **API Gateway:** Secure API management layer serving Client's mobile and web applications
- **Cryptographic Key Management Service:** HSM-based key management for payment authentication
- **24/7 Monitoring and Alerting:** Real-time platform monitoring with automated incident escalation

### 1.2 Classification

The Client designates these services as **critical or important functions** under DORA Article 28. Provider acknowledges this classification and agrees to the obligations applicable to critical ICT third-party service providers.

---

## ARTICLE 2 — DATA PROCESSING AND PROTECTION

### 2.1 Categories of Data

Provider processes the following categories of data on behalf of Client:
- Payment card data (PAN, CVV references)
- Transaction records and audit logs
- Cryptographic keys and certificates
- Customer authentication tokens

### 2.2 Subprocessors

Provider uses the following approved subprocessors:
- Amazon Web Services EMEA SARL (infrastructure)
- Thales Group (HSM hardware maintenance)

Client consent is required for new subprocessors. Provider shall give 30 days' written notice before engaging new subprocessors.

---

## ARTICLE 3 — PERFORMANCE AND SERVICE LEVELS

### 3.1 Availability Commitments

| Service Component | Availability SLA | Measurement Period |
|---|---|---|
| Core Payment Processing | 99.95% | Calendar month |
| API Gateway | 99.95% | Calendar month |
| Key Management Service | 99.99% | Calendar month |
| Monitoring Platform | 99.9% | Calendar month |

### 3.2 Recovery Objectives

In the event of a service disruption:

- **Recovery Time Objective (RTO):** Core payment processing shall be restored within **2 hours** of incident declaration for P1 severity incidents

Provider shall document and maintain recovery procedures aligned with the RTO commitments above. Recovery Point Objectives will be defined in the Business Continuity Plan referenced in Article 7.

### 3.3 Performance Monitoring

Provider shall provide Client with monthly performance reports including:
- SLA compliance percentage
- Incident log with root cause analysis for P1/P2 incidents
- Capacity utilization metrics
- Security event summary

---

## ARTICLE 4 — SECURITY REQUIREMENTS

### 4.1 Information Security Controls

Provider maintains certification under ISO/IEC 27001:2022. Annual recertification audit reports shall be shared with Client within 30 days of completion.

Provider shall maintain:
- Multi-factor authentication for all administrative access
- End-to-end encryption (TLS 1.3+) for all data in transit
- AES-256 encryption for data at rest
- Network segmentation separating Client environment from other customers
- Regular penetration testing (minimum annually) with results shared with Client

### 4.2 Access Management

Provider shall:
- Apply principle of least privilege to all service accounts
- Conduct quarterly access reviews
- Remove Client access within 24 hours of termination of any Provider employee
- Maintain immutable audit logs of all administrative actions

### 4.3 Vulnerability Management

Provider shall patch critical vulnerabilities (CVSS 9.0+) within 48 hours and high vulnerabilities (CVSS 7.0–8.9) within 14 days of public disclosure.

---

## ARTICLE 5 — INCIDENT MANAGEMENT AND NOTIFICATIONS

### 5.1 Incident Classification

| Severity | Definition |
|---|---|
| P1 Critical | Complete service unavailability or data breach |
| P2 Major | Degraded service affecting >10% of transactions |
| P3 Minor | Partial degradation with workaround available |

### 5.2 Notification Timelines

Provider shall notify Client of incidents as follows:

- **P1 (Critical):** Initial notification within **4 hours** of detection
- **P2 (Major):** Notification within 8 hours of detection
- **P3 (Minor):** Notification within 24 hours or next business day

For P1 incidents, Provider shall provide:
- Initial alert via automated monitoring integration
- Written incident notification to Client's incident response team
- Hourly status updates until resolution
- Post-incident report within 5 business days

### 5.3 Regulatory Notifications

Provider shall cooperate with Client to support Client's obligations under DORA Article 19 for major ICT-related incident reporting to Finanstilsynet. Provider shall provide all technical information and evidence required by Client within the timelines mandated by the applicable regulatory reporting windows.

Specifically, Provider shall furnish initial incident information to Client within **4 hours** of classification as a major ICT incident to enable Client's initial notification to Finanstilsynet.

---

## ARTICLE 6 — AUDIT AND INSPECTION RIGHTS

### 6.1 Audit Rights

Client, its designated auditors, and competent authorities (including Finanstilsynet and the European Banking Authority) shall have the right to:

a) Conduct on-site and remote audits of Provider's facilities and systems  
b) Review documentation, logs, and evidence related to services provided  
c) Interview Provider personnel with appropriate safeguards  
d) Access audit reports from Provider's third-party auditors (SOC 2 Type II, ISO 27001)

### 6.2 Notice and Coordination

Client shall provide minimum 15 business days' notice for planned audits. Emergency audits may be conducted on 24 hours' notice. Provider shall not unreasonably restrict audit access.

### 6.3 Cost Allocation

Audit costs are borne by Client except where audit reveals material non-compliance, in which case Provider bears reasonable audit costs.

---

## ARTICLE 7 — BUSINESS CONTINUITY AND DISASTER RECOVERY

### 7.1 Business Continuity Plan

Provider maintains a Business Continuity Plan (BCP) covering all services under this Agreement. The BCP shall be reviewed and updated at minimum annually and following any material change to the ICT environment.

Provider shall share the BCP executive summary with Client upon request. Full BCP access is available to Client's designated auditors under confidentiality obligations.

### 7.2 Disaster Recovery

Provider maintains an active-passive disaster recovery configuration with geographic separation between primary (Copenhagen) and secondary (Frankfurt) data centers.

Recovery objectives:  
- RTO: 2 hours (payment processing), 4 hours (non-critical components)
- RPO: defined in the BCP technical annex

### 7.3 Business Continuity Testing

Provider conducts business continuity and disaster recovery tests in accordance with its internal testing programme. Provider shall share test results summaries with Client annually. The testing schedule is determined by Provider's internal risk management processes.

---

## ARTICLE 8 — DATA PORTABILITY AND EXIT

### 8.1 Exit Assistance

Upon termination or expiry of this Agreement for any reason, Provider shall provide:
- 90-day transition assistance period
- Full data export in agreed formats within 30 days of termination notice
- Technical documentation sufficient for migration to alternative provider
- Knowledge transfer sessions (up to 40 hours included)

### 8.2 Data Deletion

Following successful data export confirmation by Client, Provider shall securely delete all Client data within 60 days and provide written certification of deletion.

---

## ARTICLE 9 — SUBCONTRACTING

### 9.1 Approved Subcontractors

Provider may engage subcontractors listed in Schedule C without prior approval from Client. Engagement of unlisted subcontractors requires 30 days' prior written notice and Client's approval, not to be unreasonably withheld.

### 9.2 Flow-Down Requirements

All security, audit, and incident notification obligations in this Agreement shall be flowed down to material subcontractors. Provider remains fully responsible for subcontractor performance.

---

## ARTICLE 10 — LIABILITY AND INDEMNIFICATION

### 10.1 Limitation of Liability

Provider's aggregate liability under this Agreement shall not exceed twelve (12) months of fees paid by Client in the preceding year. This limitation does not apply to gross negligence, wilful misconduct, or data protection violations.

---

## ARTICLE 11 — GOVERNING LAW AND DISPUTE RESOLUTION

This Agreement is governed by Danish law. Disputes shall be resolved by arbitration under the Danish Institute of Arbitration rules, Copenhagen.

---

## SCHEDULE A — SERVICE LEVEL CREDITS

SLA credits apply when availability falls below committed thresholds:
- 99.95%–99.90%: 5% monthly fee credit
- Below 99.90%: 10% monthly fee credit

---

*Signed on behalf of NordicPay Solutions A/S:*  
___________________________  
CFO, NordicPay Solutions A/S

*Signed on behalf of CloudCore Infrastructure ApS:*  
___________________________  
CEO, CloudCore Infrastructure ApS
