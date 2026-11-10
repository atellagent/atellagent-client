Copyright (c) 2026 Atellagent, Inc. All rights reserved.

# Atellagent Client Library License

**Version 1.0 — Effective August 1, 2026**

This License Agreement ("License") governs your use of the Atellagent client library and any accompanying proxy components, source code, object code, documentation, and related materials made available in this repository (collectively, the "Software"). The Software is owned by Atellagent, Inc. ("Atellagent," "we," "us").

**The Software is source-available, not open source.** You may read it. You may run it. You may not redistribute it, resell it, or use it to build a competing product. By downloading, copying, installing, or using the Software, you agree to this License. If you do not agree, do not use the Software.

---

## 1. Pre-Production Software; No Production Use

The Software is provided in a **pre-production, best-effort development state**.

Atellagent has not completed penetration testing, SOC 2, ISO 27001, or any other third-party security or compliance certification of the Software. You acknowledge that the Software:

1. is **not intended for use with production systems, production data, or production credentials**;
2. is provided **"AS IS" and "AS AVAILABLE"**, without warranties of any kind; and
3. may contain errors, defects, vulnerabilities, or incomplete functionality.

You assume all risk arising from your use of the Software in any environment, including any production environment, notwithstanding this notice.

## 2. License Grant

Subject to your compliance with this License, Atellagent grants you a limited, non-exclusive, non-transferable, non-sublicensable, revocable license to:

1. download, copy, and internally install the Software;
2. use the Software within your own applications and internal environments; and
3. read and inspect the source code for the purpose of evaluating, integrating, and operating the Software.

This is a license to **use**, not a license to distribute. No other rights are granted, expressly or by implication.

## 3. Restrictions

You may **not**:

1. **redistribute, publish, sublicense, sell, rent, lease, or lend** the Software, in whole or in part, in original or modified form;
2. **create or distribute derivative works** based on the Software;
3. **offer the Software, or any substantial portion of it, as a hosted, managed, or software-as-a-service offering** to any third party;
4. **use the Software to develop, train, or improve a competing product or service** in the field of AI agent governance, authorization, or policy enforcement;
5. **reverse engineer, decompile, or disassemble** any object code or compiled components, or attempt to derive the underlying methods, algorithms, or policy-evaluation logic except to the limited extent such restriction is prohibited by applicable law;
6. use the Software on behalf of, or for the benefit of, a competitor of Atellagent;
7. make any false or misleading statement of fact regarding the Software, Atellagent, or Atellagent's certifications, compliance status, or security posture;
8. use Atellagent's name, logo, or trademarks in any public statement, marketing material, or customer communication without Atellagent's prior written consent;
9. **remove, alter, or obscure** any copyright, trademark, patent, or other proprietary notice contained in the Software;
10. use the Software in violation of any applicable law, export control regulation, or sanctions program (see Section 7); or
11. use the Software to circumvent, disable, or interfere with any usage limit, entitlement, security control, or authentication mechanism, including any control governing connection to the Atellagent hosted cluster.

**Benchmarking and evaluation.** You may test and evaluate the Software. If you publish any benchmark, performance test, comparison, or evaluation, you will identify the version tested, describe the methodology used, and note that the Software is pre-production. This License does not otherwise restrict your ability to publish your honest assessment of the Software.

**Security research.** Security research conducted in good faith and in accordance with the vulnerability disclosure policy in `SECURITY.md` is permitted and welcome. Atellagent will not pursue legal action against researchers acting within that policy. Reports go to security@atellagent.com.

## 4. Standalone and Connected Operation

The Software may be operated in two modes:

**Standalone.** The Software runs entirely within your environment, maintains a local decision log, and does not connect to any Atellagent-operated infrastructure. This License alone governs standalone use, and the license granted in Section 2 continues for as long as you comply with this License.

**Connected.** The Software may be configured to connect to the Atellagent hosted cluster, which requires an Atellagent-provisioned certificate and a registered account. **Connected use is additionally governed by the Atellagent Customer Agreement**, which you must accept before proceeding after first sign-in. In the event of a conflict between this License and the Customer Agreement with respect to connected use, the Customer Agreement controls.

You may convert a standalone installation to connected operation at any time. Doing so requires acceptance of the Customer Agreement.

## 5. Ownership and Intellectual Property

The Software is licensed, not sold. Atellagent retains all right, title, and interest in and to the Software, including all intellectual property rights.

The Software incorporates technology that is the subject of one or more pending United States patent applications. **No patent license is granted under this License**, whether express, implied, by estoppel, or otherwise, except to the limited extent strictly necessary to exercise the use rights expressly granted in Section 2.

"Atellagent" and the Atellagent logo are trademarks of Atellagent, Inc. This License grants no right to use Atellagent's name, logos, or trademarks.

## 6. Third-Party Components

The Software may include third-party open-source components, which are licensed under their own terms. Those terms are set out in the `THIRD_PARTY_NOTICES.md` file in this repository and, to the extent they conflict with this License, govern your use of those components only.

## 7. Export Control and Restricted Parties

The Software may be subject to United States export control laws, including the Export Administration Regulations, and to economic sanctions programs administered by the U.S. Department of the Treasury's Office of Foreign Assets Control.

You represent and warrant that you are **not**:

1. located in, ordinarily resident in, or organized under the laws of any country or region subject to comprehensive U.S. sanctions or embargo;
2. an individual or entity identified on any U.S. government restricted party list, including the Specially Designated Nationals and Blocked Persons List, the Denied Persons List, the Entity List, or the Unverified List;
3. owned or controlled by, or acting on behalf of, any such individual or entity; or
4. otherwise prohibited from receiving the Software under applicable law.

You will not export, re-export, transfer, or make the Software available, directly or indirectly, to any prohibited destination, entity, or end use, including any use related to weapons of mass destruction or military end uses prohibited by applicable regulation.

Atellagent may restrict availability of the Software or the hosted cluster in any jurisdiction at its sole discretion.

## 8. Feedback

If you provide Atellagent with suggestions, bug reports, or other feedback regarding the Software, you grant Atellagent a perpetual, irrevocable, worldwide, royalty-free license to use and incorporate that feedback without restriction or obligation to you.

## 9. No Support

Atellagent has **no obligation** to provide maintenance, updates, bug fixes, or technical support for the Software under this License. Support, where offered, is available only under a paid subscription to the Atellagent hosted cluster and is described in the Customer Agreement.

## 10. No Compliance Warranty

Atellagent makes no representation that the Software satisfies, supports, or assists you in satisfying any legal, regulatory, industry, or contractual compliance obligation, including under the EU General Data Protection Regulation, the EU AI Act, the Digital Operational Resilience Act, the NIST AI Risk Management Framework, SOC 2, ISO/IEC 27001, HIPAA, PCI DSS, or FedRAMP.

Atellagent holds **no certification, attestation, or audit report** under any such framework.

You are solely responsible for determining whether your use of the Software meets your own compliance obligations and for obtaining any required assessments independently.

Any mapping, documentation, or descriptive material Atellagent provides referencing a compliance framework is **informational only** and is not a representation of compliance or of fitness for compliance purposes.

## 11. Disclaimer of Warranties

THE SOFTWARE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTY OF ANY KIND, EXPRESS, IMPLIED, OR STATUTORY, INCLUDING WITHOUT LIMITATION ANY WARRANTY OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, NON-INFRINGEMENT, ACCURACY, UPTIME, OR SECURITY.

ATELLAGENT DOES NOT WARRANT THAT THE SOFTWARE WILL BE UNINTERRUPTED, ERROR-FREE, SECURE, OR THAT IT WILL DETECT, PREVENT, OR BLOCK ANY PARTICULAR ACTION, THREAT, POLICY VIOLATION, OR UNAUTHORIZED BEHAVIOR. **The Software is a control mechanism, not a guarantee of correct outcomes.** You remain solely responsible for the design, configuration, testing, and supervision of your own systems and agents.

## 12. Limitation of Liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, ATELLAGENT WILL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES, OR FOR ANY LOSS OF PROFITS, REVENUE, DATA, GOODWILL, OR BUSINESS INTERRUPTION, ARISING OUT OF OR RELATING TO THE SOFTWARE, WHETHER IN CONTRACT, TORT, OR ANY OTHER THEORY, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

ATELLAGENT'S TOTAL AGGREGATE LIABILITY ARISING OUT OF OR RELATING TO THIS LICENSE WILL NOT EXCEED ONE HUNDRED U.S. DOLLARS (US$100).

Because the Software is provided free of charge under this License, you acknowledge that this allocation of risk is a fundamental basis of the bargain and that Atellagent would not make the Software available on any other terms.

## 13. Term and Termination

This License is effective until terminated.

This License terminates **automatically and immediately** if you breach any of its terms. Atellagent may also terminate this License at any time, with or without cause, upon notice.

Upon termination, you must cease all use of the Software and destroy all copies in your possession or control.

Sections 1, 3, 5, 7, 8, 10, 11, 12, 13, and 14 survive termination.

## 14. General

**Governing Law and Venue.** This License is governed by the laws of the State of California, without regard to its conflict-of-laws principles. The parties consent to exclusive jurisdiction and venue in the state and federal courts located in **Sonoma County, California**, and waive any objection to that forum.

**U.S. Government Rights.** The Software is "commercial computer software" as defined in applicable federal acquisition regulations. Use, duplication, or disclosure by the U.S. Government is subject to the restrictions in this License.

**Modification of Terms.** Atellagent may publish updated versions of this License. Updated terms apply to versions of the Software released after the update. Your continued use of a given version remains governed by the License distributed with that version. Published versions are archived and remain available for reference.

This License is versioned independently of the Software. The version of this License does not correspond to, and does not change with, the release version of the Software.

**Severability.** If any provision of this License is held unenforceable, that provision will be limited to the minimum extent necessary and the remaining provisions will remain in full force.

**Entire Agreement.** This License, together with the Customer Agreement where applicable, constitutes the entire agreement between you and Atellagent regarding the Software.

---

Questions regarding this License: **legal@atellagent.com**
