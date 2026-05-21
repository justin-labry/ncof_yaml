#!/usr/bin/env python3
"""Rule-based decision (simplified, single-source): per-gNB2 power state from
a WLAN-only metric, paired with a CN-side QoS policy toggle.

This is the **baseline rule-based algorithm** for the ModuTec NCOF
implementation, intended as the production-ready fallback / pre-RL
baseline before the Korea University reinforcement-learning model is
integrated. Unlike ``decide_cell_power.py`` (which fuses 6 notifications
through 5 rules R1-R5), this script consumes a single WLAN/Non-3GPP
throughput signal and produces both the cell power decision and the
matching QoS policy in one pass.

Inputs (single source):
    A 12p_c-shaped notification from UPF -> NCOF carrying
    ``USER_DATA_USAGE_MEASURES``. In the simplified baseline the script
    treats the aggregate DL throughput across all reported flows as the
    Non-3GPP/WLAN load proxy. When ModuTec wires the dedicated
    ``WLAN_PERFORMANCE`` notification (subscribed in 1p), swap
    ``extract_wlan_dl_mbps`` for the new shape.

Outputs (two notifications):
    1. 15f shape (``NncofEventsSubscriptionNotification``, NCOF -> RICF):
       gNB2 cell power state (``DEEP_SLEEP`` / ``ACTIVE``).
    2. 14_e shape (``NncofEventsSubscriptionNotification``, NCOF -> PCF):
       Per-flow QoS policy with ``gbrDl``/``mbrDl`` flipped per the rule.

Decision rule (single-threshold band with hysteresis):

    metric_mbps = sum of DL throughput from 12p_c
    if metric_mbps >= TH_HIGH:        gNB2 -> ACTIVE       (WLAN saturated; cannot absorb offload)
    elif metric_mbps <= TH_LOW:       gNB2 -> DEEP_SLEEP   (WLAN has spare; offload feasible)
    else:                             gNB2 -> previous_state

Direction rationale: high WLAN throughput means Non-3GPP is already busy
and has little spare capacity to absorb UEs offloaded from gNB2, so gNB2
must stay ACTIVE. Low WLAN throughput means there is headroom on the
Wi-Fi path, so gNB2 can sleep safely. This matches the spare-capacity
logic of ``tools/decide_cell_power.py`` rule R4.

QoS mapping per output policy set (driven by the new gNB2 state):

    | ratType | spatialValidity | DEEP_SLEEP        | ACTIVE             |
    |---------|-----------------|-------------------|--------------------|
    | NR      | gNB2 (000002)   | gbr=0,   mbr=0    | gbr=1000, mbr=1000 |
    | WLAN    | n3IwfId         | gbr=1000, mbr=1000| gbr=0,   mbr=0     |
    | NR      | gNB1 (000001)   | (untouched)       | (untouched)        |

Run::

    # One-shot from PoC sample JSONs (assumes previous state = ACTIVE):
    python tools/decide_wlan_gnb2_rule.py

    # Override previous state and thresholds:
    python tools/decide_wlan_gnb2_rule.py --prev-state DEEP_SLEEP --th-high 800 --th-low 400

    # Use a custom WLAN measurement file:
    python tools/decide_wlan_gnb2_rule.py --wlan-input path/to/12p_c.json

Outputs are emitted to stdout as two JSON documents separated by a marker
line (``===15f===`` / ``===14e===``) so they can be piped or split.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Tunables (PoC defaults; override via CLI)
# ---------------------------------------------------------------------------

TH_HIGH_MBPS = 500.0       # >= : ACTIVE       (WLAN busy -> can't offload, keep gNB2 awake)
TH_LOW_MBPS = 300.0        # <= : DEEP_SLEEP   (WLAN idle -> can offload, sleep gNB2)

GNB2_VALUE = "000002"      # which gNB the rule controls
GNB1_VALUE = "000001"      # primary gNB; never affected by this rule

GBR_HIGH_MBPS = 1000.0     # "fully on" QoS target
GBR_OFF_MBPS = 0.0         # "fully off" QoS target

SUB_DURATION = timedelta(seconds=60)
NCOF_RESOURCE_BASE = "https://6g-i2p.etri.re.kr/scenario2/ncof"
RICF_NF_ID = "ricf-uuid-001"

DEFAULT_PLMN = {"mcc": "001", "mnc": "999"}
DEFAULT_NID = "00000000001"

# Cell-power RF presets (kept aligned with tools/decide_cell_power.py)
CELL_POWER_PRESETS: dict[str, dict[str, str]] = {
    "DEEP_SLEEP": {
        "_cellTxPower": "20dBm",
        "_ssPbchBlockPower": "15dBm",
        "_siBlockPower": "10dBm",
        "_pdschBlockPower": "18dBm",
        "_pdcchBlockPower": "12dBm",
        "_csiRsPowerOffset": "3dB",
        "_qRxLevMin": "-120dBm",
        "_cellIndividualOffset": "0dB",
    },
    "ACTIVE": {
        "_cellTxPower": "30dBm",
        "_ssPbchBlockPower": "25dBm",
        "_siBlockPower": "20dBm",
        "_pdschBlockPower": "28dBm",
        "_pdcchBlockPower": "22dBm",
        "_csiRsPowerOffset": "3dB",
        "_qRxLevMin": "-120dBm",
        "_cellIndividualOffset": "0dB",
    },
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_mbps(s: Any) -> float:
    """Parse '150 Mbps' / '1.2 Gbps' / '500 kbps' -> float Mbps. None/'' -> 0."""
    if s is None:
        return 0.0
    m = re.match(r"\s*([\d.]+)\s*([KMG]?bps)?\s*", str(s), re.IGNORECASE)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = (m.group(2) or "Mbps").lower()
    scale = {"gbps": 1000.0, "mbps": 1.0, "kbps": 1e-3, "bps": 1e-6}.get(unit, 1.0)
    return val * scale


def extract_wlan_dl_mbps(notif_12p_c: dict) -> float:
    """Sum DL throughput from a 12p_c-shaped USER_DATA_USAGE_MEASURES notification.

    Simplification: the rule baseline treats every reported flow's DL
    throughput as Non-3GPP/WLAN load. When ModuTec subscribes to the
    dedicated ``WLAN_PERFORMANCE`` event (1p) and receives BSSID-specific
    measurements, replace this function with a WLAN-tagged variant.
    """
    total = 0.0
    for item in notif_12p_c.get("notificationItems", []):
        if item.get("eventType") != "USER_DATA_USAGE_MEASURES":
            continue
        for m in item.get("userDataUsageMeasurements", []):
            tps = m.get("throughputStatisticsMeasurement", {})
            total += _parse_mbps(tps.get("dlAverageThroughput"))
    return total


def _now_iso_kst() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def decide_gnb2_state(
    metric_mbps: float,
    previous_state: str = "ACTIVE",
    th_high: float = TH_HIGH_MBPS,
    th_low: float = TH_LOW_MBPS,
) -> str:
    """Hysteresis rule.

    metric >= th_high  -> ACTIVE       (WLAN saturated, gNB2 must stay awake)
    metric <= th_low   -> DEEP_SLEEP   (WLAN has headroom, gNB2 can sleep)
    otherwise          -> previous_state  (stay put in the hysteresis band)
    """
    if metric_mbps >= th_high:
        return "ACTIVE"
    if metric_mbps <= th_low:
        return "DEEP_SLEEP"
    return previous_state


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------


def _time_window(start_iso: str) -> tuple[str, str]:
    stop_iso = (datetime.fromisoformat(start_iso) + SUB_DURATION).isoformat(
        timespec="seconds"
    )
    return start_iso, stop_iso


def build_15f_cell_power(
    state: str,
    decision_iso: str,
    sub_id: str,
    corr_id: str,
) -> list[dict]:
    """Build a 15f-shape NCOF->RICF notification announcing the new gNB2 state."""
    start, stop = _time_window(decision_iso)
    return [
        {
            "subscriptionId": sub_id,
            "notifCorrId": corr_id,
            "resourceUri": f"{NCOF_RESOURCE_BASE}/{sub_id}",
            "transEvents": ["_CELL_POWER_CTRL"],
            "eventNotifications": [
                {
                    "event": "_CELL_POWER_CTRL",
                    "timeStampGen": start,
                    "start": start,
                    "expiry": stop,
                    "anaMetaInfo": {
                        "numSamples": 1,
                        "dataWindow": {"startTime": start, "stopTime": stop},
                        "dataStatProps": ["NO_OUTLIERS"],
                        "strategy": "GRADIENT",
                        "_optScore": 1.0,
                        "nfIds": [RICF_NF_ID],
                    },
                    "pauseInd": False,
                    "resumeInd": False,
                    "cancelAccuInd": False,
                    "_ctrlEventInd": True,
                    "_cellPowerCtrlOptInfos": [
                        {
                            "_cellPowerCtrlInfo": {
                                "_tsStart": start,
                                "_tsDuration": str(
                                    int(SUB_DURATION.total_seconds())
                                ),
                                "_ratFreq": {
                                    "allFreq": True,
                                    "allRat": False,
                                    "ratType": "NR",
                                },
                                "_confidence": 0.95,
                                "_optScore": 1.0,
                                "_cellPowerParamSets": [
                                    {
                                        "_ctrlTimeWin": {
                                            "startTime": start,
                                            "stopTime": stop,
                                        },
                                        "_validityPeriod": {
                                            "startTime": start,
                                            "stopTime": stop,
                                        },
                                        "_spatialValidity": {
                                            "gRanNodeIds": [
                                                {
                                                    "plmnId": dict(DEFAULT_PLMN),
                                                    "gNbId": {
                                                        "bitLength": 24,
                                                        "gNBValue": GNB2_VALUE,
                                                    },
                                                    "nid": DEFAULT_NID,
                                                }
                                            ]
                                        },
                                        "_ratTypes": ["NR"],
                                        "_cellPowerParamSet": {
                                            "_paramSetId": (
                                                f"CELL_POWER_PARAM_SET-{start}_1"
                                            ),
                                            "_cellPowerState": state,
                                            **CELL_POWER_PRESETS[state],
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                }
            ],
        }
    ]


def _is_wlan_set(policy_set: dict) -> bool:
    return "WLAN" in (policy_set.get("ratTypes") or [])


def _is_on_gnb(policy_set: dict, gnb_value: str) -> bool:
    sv = policy_set.get("spatialValidity") or {}
    for g in sv.get("gRanNodeIds") or []:
        if (g.get("gNbId") or {}).get("gNBValue") == gnb_value:
            return True
    return False


def apply_qos_policy(template: list[dict], state: str) -> list[dict]:
    """Mutate (a copy of) a 14_e-shape template's qosParamSet.gbrDl/mbrDl per rule.

    Rules:
      - WLAN set:        gbr/mbr = HIGH if DEEP_SLEEP else OFF
      - NR + gNB2 set:   gbr/mbr = OFF  if DEEP_SLEEP else HIGH
      - NR + gNB1 set:   untouched (this rule never touches the primary cell)
    """
    out = copy.deepcopy(template)
    for top in out:
        for ev in top.get("eventNotifications", []):
            for info in ev.get("qosPolAssistInfos", []):
                for assist in info.get("qosPolAssistInfo", []):
                    for ps in assist.get("qosPolAssistSets", []):
                        _flip_one_set(ps, state)
    return out


def _flip_one_set(policy_set: dict, state: str) -> None:
    if _is_wlan_set(policy_set):
        gbr = GBR_HIGH_MBPS if state == "DEEP_SLEEP" else GBR_OFF_MBPS
    elif _is_on_gnb(policy_set, GNB2_VALUE):
        gbr = GBR_OFF_MBPS if state == "DEEP_SLEEP" else GBR_HIGH_MBPS
    else:
        return  # not affected by this rule (e.g., primary gNB1)
    qps = policy_set.setdefault("qosParamSet", {})
    qps["gbrDl"] = f"{gbr} Mbps"
    qps["mbrDl"] = f"{gbr} Mbps"


# ---------------------------------------------------------------------------
# Demo runner
# ---------------------------------------------------------------------------


REPO = Path(__file__).resolve().parent.parent
SAMPLE_DIR = REPO / "ncof_json"
DEFAULT_WLAN_INPUT = SAMPLE_DIR / "12p_c_NotificationData_from_UPF_to_NCOF_v1.0.json"
QOS_TEMPLATE_INPUT = (
    SAMPLE_DIR / "14_e_NncofEventsSubscriptionNotification_from NCOF_to_PCF_v1.0.json"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--wlan-input", type=Path, default=DEFAULT_WLAN_INPUT)
    p.add_argument("--qos-template", type=Path, default=QOS_TEMPLATE_INPUT)
    p.add_argument("--prev-state", default="ACTIVE", choices=["ACTIVE", "DEEP_SLEEP"])
    p.add_argument("--th-high", type=float, default=TH_HIGH_MBPS)
    p.add_argument("--th-low", type=float, default=TH_LOW_MBPS)
    p.add_argument(
        "--decision-time",
        default=None,
        help="ISO timestamp for the decision; default = WLAN notif timeStamp or now.",
    )
    return p.parse_args(argv)


def _decision_time_from_notif(notif: dict, fallback: str) -> str:
    for item in notif.get("notificationItems", []):
        ts = item.get("timeStamp") or item.get("startTime")
        if ts:
            return ts
    return fallback


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    wlan_notif = json.loads(args.wlan_input.read_text())
    qos_template = json.loads(args.qos_template.read_text())

    metric = extract_wlan_dl_mbps(wlan_notif)
    new_state = decide_gnb2_state(metric, args.prev_state, args.th_high, args.th_low)
    decision_iso = args.decision_time or _decision_time_from_notif(
        wlan_notif, _now_iso_kst()
    )
    sub_id = f"SUBSCRIPTION_{decision_iso}_1"
    corr_id = f"NOTIFICATION_{decision_iso}_1"

    # Diagnostics on stderr so stdout stays parseable
    print(
        f"[rule] metric={metric:.1f} Mbps  prev={args.prev_state}  "
        f"th_low={args.th_low} th_high={args.th_high}  -> new={new_state}"
        f"  (changed={new_state != args.prev_state})",
        file=sys.stderr,
    )

    # Always emit both notifications; downstream decides whether to fan out.
    cell_notif = build_15f_cell_power(new_state, decision_iso, sub_id, corr_id)
    qos_notif = apply_qos_policy(qos_template, new_state)

    print("===15f===")
    print(json.dumps(cell_notif, indent=2, ensure_ascii=False))
    print("===14e===")
    print(json.dumps(qos_notif, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
