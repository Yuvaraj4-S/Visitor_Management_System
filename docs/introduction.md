# Visitor Management

Visitor Management is an open-source, modern, and easy-to-use Visitor Management System for offices, factories, and campuses of any size. It is a complete VMS solution with everything you need to run the gate — from pre-visit invitations and host approvals, through QR-based check-in and identity verification, all the way to hospitality, conference rooms, and end-to-end compliance reporting.

## Why Visitor Management

Most organizations still run their gates on paper registers, spreadsheets, and disconnected approvals. Hosts call security, security calls reception, and nobody has a single record of who is on the premises right now.

Visitor Management brings every step of a visit — invitation, approval, gate verification, hospitality, conference room, exit — under one roof, so the host, the security officer, the hospitality team, and the facility manager are all looking at the same record.

Whether you are managing a handful of walk-ins a day or hundreds of scheduled visitors across multiple gates, the app keeps the trail clean, the approvals in the right hands, and the lobby moving — without rekeying anything.

## Key Features

- **Visitor Lifecycle**: Invite a visitor, capture their details, send them a pre-visit QR pass, run the host-approval workflow, and follow the visit from pre-registration through check-in, in-premises movements, and check-out — all on one timeline.
- **Gate Security and Identity Verification**: Scan the visitor's QR at the gate, live-capture their photo, verify against the pass photo and ID proof, screen against the active blacklist, and issue the badge — with every gate event recorded as an immutable Security Log.
- **Hospitality Management**: Plan meals, hotel stays, cab pick-up/drop, factory tours, on-site buggy transport, and greetings against the visit window. Approved Visitor Passes automatically route their hospitality requests into the Hospitality Manager's queue.
- **Conference Room Booking**: Auto-book the room when a pass is approved, clamp the meeting to the room's operating hours, validate seating capacity, and route the booking through Facility Manager approval.
- **VIP Protocol**: A dedicated VIP queue at the gate, MD/CEO notification, protocol notes carried through to the security officer, and pre-allocated meeting rooms — so priority visitors never wait in the lobby.
- **Compliance and Reporting**: Out-of-the-box reports for Active Visitors, Daily Visitor Log, Gate-Wise Count, Identity Match Audit, Compliance Overview, Daily Hospitality Schedule, Monthly Hospitality Cost, Room Utilization, and Daily Booking Schedule.
- **Role-Based Workflows**: The app runs on role-based workflows, so the right person sees the right approval at the right time.

And more.

## Under the Hood

- **Frappe Framework**: A full-stack web application framework written in Python and JavaScript. The framework provides a robust foundation for the app, including the database abstraction layer, user authentication, workflow engine, and REST API.
- **ERPNext Integration**: Visitors are linked to ERPNext Employees (host, security officer, tour guide) and Suppliers (hotel, cab vendor), so the existing org chart and vendor master are reused — no duplicate data.
- **Workflow Engine**: Frappe's workflow engine drives approval lanes for Visitor Pass, Hospitality Request, and Conference Room Booking, with role-gated transitions and email notifications at every step.

## Installation

To install/setup the app, follow the guidelines in the [README](../README.md).

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/Yuvaraj4-S/Visitor_Management_System.git --branch develop
bench install-app visitormanagement
```

## Learning and Community

- **GitHub Repository** — source code, issues, and releases at [github.com/Yuvaraj4-S/Visitor_Management_System](https://github.com/Yuvaraj4-S/Visitor_Management_System).
- **README** — quick start, installation, contributing guide.
- **Visitor Management Documentation** — full user guide ([visitor-management-documentation.pdf](visitor-management-documentation.pdf)).
- **Frappe School** — learn the Frappe Framework and ERPNext from the courses by the maintainers or the community.
- **Frappe User Forum** — engage with the wider community of Frappe and ERPNext users.
