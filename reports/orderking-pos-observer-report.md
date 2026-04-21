# OrderKing POS Observer: capture findings and publish-path report

Source data: `/home/iggut/workspace/cap`
Runs reviewed:
- `slave/2026-04-20T14-34-35`
- `master/2026-04-20T14-39-38`

Scope note:
- Printer / receipt / spooler activity was excluded on purpose.
- Focus is order publishing / submission to backend server communication.

## Executive summary

The captures strongly support that the POS publishes orders through the application itself and a persistent backend connection, not through printing.

The most important evidence is:
- a `publish_clicked` marker at `2026-04-20T17:44:33.250000+00:00`
- a foreground switch to `TQuoteFrm` with title `Purchases` immediately after that publish marker
- a return to the main POS form `TPOS_frm` (`VapeKing Store 6 Station Master`)
- sustained TCP activity from `VapeKing.exe` to `108.163.128.4:3306`

The backend endpoint is consistent with a MySQL-style server. In the reviewed windows, the POS process remained connected continuously; I did not see evidence that publishing was driven by a printer or a receipt spool.

## What happened around the important times

### 17:44:33 publish event

Observed sequence in the master capture:
- `2026-04-20T17:44:33.250000+00:00` — `publish_clicked`
- `2026-04-20T17:44:44.795208+00:00` — foreground window `TQuoteFrm` / `Purchases`
- `2026-04-20T17:44:58.911203+00:00` — foreground returns to `TPOS_frm` / `VapeKing Store 6 Station Master`

Interpretation:
- This is the clearest publish/submission moment in the capture set.
- The UI flow suggests the order moves through the Purchases/Quote form during publish.
- The backend connection to `108.163.128.4:3306` is active throughout the window, so this likely corresponds to server-side order submission or sync.

### 15:56 order flow

Foreground window sequence around 15:56:
- `TMsgDialog`
- `TPOS_frm`
- `TEnterPmtFrm`
- `TChangeDlg`
- back to `TPOS_frm`

Interpretation:
- This looks like a completed transaction path with payment/change dialogs.
- It is consistent with an order being finalized and pushed through the POS workflow.
- Network to `108.163.128.4:3306` was active in the same period.

### 17:01

I could not isolate a distinct foreground-window milestone exactly at 17:01 from the sampled window-focus slice, but the backend connection to `108.163.128.4:3306` remained active before and after that time.

Interpretation:
- This is likely another publish / sync / transaction point in the same cadence.
- The available evidence is weaker than for 17:44:33 and 15:56.

### 13:44

The capture data provided for review begins after this time, so I could not verify the 13:44 event directly from these runs.

## Backend and transport findings

- Process: `VapeKing.exe`
- PID observed: `8140`
- Backend endpoint: `108.163.128.4:3306`
- Transport: TCP, established snapshots repeated through the reviewed windows

This is the strongest technical clue for how the POS publishes orders. The app appears to keep a persistent backend link and likely writes order state through that connection rather than using a side-channel like printing.

## “Another software” hypothesis

I did not find evidence of a separate visible order-entry application taking focus during the reviewed windows.

What I did see:
- `VapeKing.exe` as the foreground application during the publish and payment sequences
- background system noise from browsers and helper processes (`chrome.exe`, `msedge.exe`, `msedgewebview2.exe`, `TeamViewer.exe`, `TeamViewer_Service.exe`, `WorkflowAppControl.exe`)

Current conclusion:
- The visible order workflow is still happening inside VapeKing.
- If orders are being created from another software, that software was not brought to the foreground in these windows, or it interacts indirectly with the same backend rather than with the POS UI.
- Sales/reporting is another recurring workflow in both captures, and it is a strong feature candidate to clone for a companion app.

## Practical implication for creating orders from other software

Based on these captures, the promising route is not receipt printing. It is the backend publish path:
- identify the backend data model / tables / protocol behind `108.163.128.4:3306`
- inspect how the POS transitions from `TPOS_frm` to `TQuoteFrm` during publish
- map the transaction fields written just before and after `publish_clicked`

If the goal is to originate orders from another app, the best fit is to emulate the POS backend transaction pattern, not the printer path.

## Next analysis step

Recommended follow-up:
1. Correlate `publish_clicked`, `TQuoteFrm`, and `TEnterPmtFrm` with the exact network snapshot cadence around each event.
2. Pull any file or registry writes that happen immediately before/after publish.
3. Identify whether the backend is true MySQL or a MySQL-compatible application server.
4. Reverse the order payload shape from captured traffic or local app state changes.

## Bottom line

The captures point to a server-backed publish flow in VapeKing, centered on the Purchases/Quote form and a persistent connection to `108.163.128.4:3306`. Printing is not part of the evidence for order submission. I do not yet have proof of a separate external order-entry app, but the backend path is the most likely target if you want to create orders from another software.
