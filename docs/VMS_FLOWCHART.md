# Visitor Management System — How It Works

A visual guide for the whole company. Read top-to-bottom; no prior knowledge needed.

---

## 1. The Big Picture — A Visitor's Journey

This is what happens from the moment someone wants to visit your facility, to the moment they leave.

```mermaid
flowchart LR
    A([Someone needs<br/>to visit]) --> B[Host sends invitation<br/>OR Front Desk creates pass]
    B --> C{Approval<br/>required?}
    C -- Yes --> D[Goes to the right<br/>approver based on<br/>visitor type]
    C -- No --> E
    D --> E[Pass is approved]
    E --> F[Visitor arrives<br/>at the gate]
    F --> G[Security verifies<br/>ID, photo, blacklist]
    G --> H{Cleared?}
    H -- No --> X([Entry refused])
    H -- Yes --> I[Badge issued<br/>Visitor checks in]
    I --> J[Visitor meets host<br/>conducts business]
    J --> K[Visitor returns<br/>to gate]
    K --> L[Security checks out<br/>Badge returned]
    L --> Z([Visit complete])

    style A fill:#e3f2fd,stroke:#1976d2
    style Z fill:#e8f5e9,stroke:#388e3c
    style X fill:#ffebee,stroke:#c62828
    style D fill:#fff3e0,stroke:#f57c00
    style G fill:#fff3e0,stroke:#f57c00
```

---

## 2. Who Approves What — Routing by Visitor Type

Different kinds of visitors go to different approvers. The system figures this out automatically based on the **visitor type** you pick when creating the pass.

```mermaid
flowchart TD
    Start([New Visitor Pass<br/>created in Draft]) --> Type{What kind of<br/>visitor?}

    Type -- Customer --> Sales[Sales Manager<br/>reviews]
    Type -- Supplier --> Sys1[System Manager<br/>reviews]
    Type -- Contractor --> Sys2[System Manager<br/>reviews]
    Type -- Candidate<br/>job applicant --> HR[HR Manager<br/>reviews]
    Type -- VIP --> HOD[HOD<br/>reviews first]
    Type -- Other<br/>employee/general --> Auto[Auto-approved<br/>or host-only]

    HOD --> HODdec{HOD<br/>decision}
    HODdec -- Approve --> CEO[CEO<br/>final review]
    HODdec -- Reject --> Rejected

    CEO --> CEOdec{CEO<br/>decision}
    CEOdec -- Approve --> Approved
    CEOdec -- Reject --> Rejected

    Sales --> SalesDec{Decision}
    SalesDec -- Approve --> Approved
    SalesDec -- Reject --> Rejected

    Sys1 --> Sys1Dec{Decision}
    Sys1Dec -- Approve --> Approved
    Sys1Dec -- Reject --> Rejected

    Sys2 --> Sys2Dec{Decision}
    Sys2Dec -- Approve --> Approved
    Sys2Dec -- Reject --> Rejected

    HR --> HRdec{Decision}
    HRdec -- Approve --> Approved
    HRdec -- Reject --> Rejected

    Auto --> Approved

    Approved([Approved — visitor<br/>can come in])
    Rejected([Rejected — host<br/>can re-apply])

    style Start fill:#e3f2fd,stroke:#1976d2
    style Approved fill:#e8f5e9,stroke:#388e3c
    style Rejected fill:#ffebee,stroke:#c62828
    style HOD fill:#fff3e0,stroke:#f57c00
    style CEO fill:#fff3e0,stroke:#f57c00
```

**Rule of thumb**: VIPs get two-level approval (HOD then CEO). Everyone else needs one approver. The right approver is picked automatically based on the visitor type.

---

## 3. The Workflow States — Where Is My Pass Right Now?

Every Visitor Pass moves through these states. The colour tells you the situation at a glance.

```mermaid
stateDiagram-v2
    [*] --> Draft: Host creates pass

    Draft --> PendingApproval: Submit for approval

    state PendingApproval {
        [*] --> PendingSysMgr: Supplier / Contractor
        [*] --> PendingSales: Customer
        [*] --> PendingHR: Candidate
        [*] --> PendingHOD: VIP
        PendingHOD --> PendingCEO: HOD approves
    }

    PendingApproval --> Approved: Approver says yes
    PendingApproval --> Rejected: Approver says no

    Rejected --> Draft: Host re-applies

    Approved --> ItemsVerified: Security checks ID/photo
    ItemsVerified --> CheckedIn: Visitor enters
    CheckedIn --> CheckedOut: Visitor leaves
    CheckedOut --> [*]

    Approved --> NoShow: Visitor never arrived
    NoShow --> [*]
```

---

## 4. At the Gate — What Security Does

This is the operational front-line flow. Security is the gatekeeper.

```mermaid
flowchart TD
    Arrive([Visitor arrives at gate]) --> Search[Security searches by<br/>name / mobile / pass ID]
    Search --> Found{Pass found<br/>and approved?}
    Found -- No --> Refuse([Politely refuse —<br/>ask host to create pass])
    Found -- Yes --> BL{Blacklisted?}
    BL -- Yes --> Block([Block entry —<br/>notify host & manager])
    BL -- No --> Verify[Verify ID proof<br/>Take live photo<br/>Match to pass]
    Verify --> Health{Health screening<br/>required?}
    Health -- Yes --> Screen[Temperature check<br/>health questionnaire]
    Health -- No --> Issue
    Screen --> ScreenOK{Passed?}
    ScreenOK -- No --> Block
    ScreenOK -- Yes --> Issue[Issue badge<br/>Log check-in time]
    Issue --> Inside([Visitor inside facility])

    Inside --> Return[Visitor returns to gate]
    Return --> Out[Collect badge<br/>Log check-out time]
    Out --> Done([Visit complete])

    style Arrive fill:#e3f2fd,stroke:#1976d2
    style Done fill:#e8f5e9,stroke:#388e3c
    style Refuse fill:#ffebee,stroke:#c62828
    style Block fill:#ffebee,stroke:#c62828
    style Inside fill:#fff8e1,stroke:#f9a825
```

---

## 5. Hospitality Side — When Visitors Need More Than a Badge

When a visitor needs lunch, a meeting room, transport, or a guided tour, a **Hospitality Request** runs alongside the visitor pass.

```mermaid
flowchart LR
    VP[Visitor Pass<br/>approved] --> Need{Hospitality<br/>needed?}
    Need -- No --> Skip([No extra arrangements])
    Need -- Yes --> HR[Host Employee creates<br/>Hospitality Request]
    HR --> HM[Hospitality Manager<br/>reviews request]
    HM --> Approve{Approve?}
    Approve -- No --> Reject([Request denied])
    Approve -- Yes --> Plan[Arrangements made:<br/>meal / room / transport]
    Plan --> Day([On the visit day —<br/>arrangements ready])

    style VP fill:#e3f2fd,stroke:#1976d2
    style Day fill:#e8f5e9,stroke:#388e3c
    style Reject fill:#ffebee,stroke:#c62828
```

---

## 6. The Roles — Who Does What

A simple cheat-sheet of who can do what in the system.

```mermaid
flowchart LR
    subgraph Creators[Who creates passes]
        E[Employee<br/>creates pass for own visitors]
        SM[System Manager<br/>can create any pass]
        HRC[HR Manager<br/>creates Candidate passes]
        SLC[Sales Manager<br/>creates Customer passes]
    end

    subgraph Approvers[Who approves]
        SA[System Manager<br/>approves Supplier, Contractor]
        SaA[Sales Manager<br/>approves Customer]
        HA[HR Manager<br/>approves Candidate]
        HD[HOD<br/>approves VIP step 1]
        CE[CEO<br/>approves VIP step 2]
    end

    subgraph Operations[Who runs the gate]
        SEC[Security<br/>check-in / check-out<br/>verify ID and photo]
    end

    subgraph Hospitality[Who handles hospitality]
        HE[Host Employee<br/>raises hospitality requests]
        HM2[Hospitality Manager<br/>approves and arranges]
        FM[Facility Manager<br/>approves room bookings]
    end

    style Creators fill:#e3f2fd
    style Approvers fill:#fff3e0
    style Operations fill:#fce4ec
    style Hospitality fill:#f3e5f5
```

---

## 7. A Real-World Example — VIP Visit, Step by Step

To make it concrete, here is one full journey for a VIP visit.

```mermaid
sequenceDiagram
    participant H as Host (Employee)
    participant V as Visitor Pass
    participant HOD as HOD
    participant CEO as CEO
    participant S as Security
    participant HM as Hospitality Mgr

    H->>V: Create pass — type: VIP
    V->>HOD: Routes to HOD for approval
    HOD->>V: Approves (state: Pending CEO)
    V->>CEO: Routes to CEO for final approval
    CEO->>V: Approves (state: Approved)

    Note over H,HM: Day before visit
    H->>HM: Create Hospitality Request<br/>(lunch, meeting room, transport)
    HM->>HM: Review and arrange

    Note over V,S: Visit day
    V->>S: Visitor arrives at gate
    S->>S: Verify ID, take photo,<br/>check blacklist
    S->>V: Issue badge (state: Checked-In)
    Note over H,V: Visitor meets host,<br/>conducts meeting
    V->>S: Visitor returns to gate
    S->>V: Collect badge (state: Checked-Out)
```

---

## 8. Quick Glossary

| Term | What it means |
|---|---|
| **Visitor Pass** | The main document. Tracks one visit from creation to check-out. |
| **Visitor Type** | Customer, Supplier, Contractor, Candidate, VIP, etc. Decides who approves. |
| **Host** | The internal employee the visitor is coming to meet. |
| **Approval Workflow** | The automatic routing of the pass to the correct approver. |
| **Security Log** | Each check-in and check-out is recorded as a separate Security Log entry. |
| **Hospitality Request** | A side-document for lunch, rooms, or transport. Optional. |
| **Blacklist** | A list of people not allowed entry. Checked at the gate. |
| **Badge** | The physical pass given at check-in, returned at check-out. |

---

## 9. The One-Page Summary

If you remember nothing else, remember this:

1. **Host or front desk creates the pass.**
2. **System routes it to the right approver based on visitor type.**
3. **Approver says yes or no.**
4. **Security verifies the visitor at the gate.**
5. **Visitor checks in, does their business, checks out.**
6. **Hospitality runs in parallel if extras are needed.**

That's the whole system.
