#!/usr/bin/env python3
"""Rule-based decision: per-gNB cell power state, output in 15f format.

Decides DEEP_SLEEP vs ACTIVE per 3GPP gNB based on aggregated NCOF inputs from
the 6 'p'-variant notification JSONs (8p, 9p, 10p_a, 11p_b, 12p_c, 13p_d), and
emits a control notification matching the 15f
(NncofEventsSubscriptionNotification, NCOF -> RICF) JSON shape so the result
can be sent directly to RICF.

Only gNBs whose decision differs from their current power state are included
in the output -- a no-op decision is omitted.

Run:
    python tools/decide_cell_power.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Configurable thresholds (PoC defaults; tune per scenario)
DEFAULT_THRESHOLDS = {
    "non3gpp_peak_dl_mbps": 300.0,
    "non3gpp_peak_ul_mbps": 100.0,
    "qos_delay_headroom_ms": 2.0,
    "qos_plr_headroom_pct": 1.0,
    "min_gnb_power_for_sleep_mw": 5.0,
    "primary_gnb_ids": ["000001"],
}

# Cell power parameter presets per target state (PoC values)
CELL_POWER_PARAM_PRESETS = {
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

DEFAULT_PLMN_ID = {"mcc": "001", "mnc": "999"}
DEFAULT_NID = "00000000001"
RICF_NF_ID = "ricf-uuid-001"
NCOF_RESOURCE_BASE = "https://6g-i2p.etri.re.kr/scenario2/ncof"
SUBSCRIPTION_DURATION = timedelta(seconds=60)


def _parse_bitrate_mbps(s):
    if s is None:
        return None
    m = re.match(r"\s*([\d.]+)\s*([KMG]?bps)?\s*", str(s), re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "Mbps").lower()
    return {"gbps": 1000.0, "mbps": 1.0, "kbps": 1e-3, "bps": 1e-6}.get(unit, 1.0) * val


def _parse_power_mw(s):
    if s is None:
        return None
    m = re.match(r"\s*([\d.]+)\s*([mkM]?W)?\s*", str(s))
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "mW").lower()
    return {"kw": 1e6, "w": 1e3, "mw": 1.0}.get(unit, 1.0) * val


def _gnb_id_value(loc):
    try:
        return loc["nrLocation"]["globalGnbId"]["gNbId"]["gNBValue"]
    except (KeyError, TypeError):
        return None


def _gnb_global_id(loc):
    """Return the full globalGnbId object preserving plmnId/gNbId/nid."""
    try:
        gid = loc["nrLocation"]["globalGnbId"]
        return {
            "plmnId": dict(gid.get("plmnId", DEFAULT_PLMN_ID)),
            "gNbId": dict(gid.get("gNbId", {})),
            "nid": gid.get("nid", DEFAULT_NID),
        }
    except (KeyError, TypeError):
        return None


def extract_qos_per_supi(notif_8p):
    """8p QOS_MONITORING items -> {supi: per-UE QoS measurements (3GPP only)}."""
    qos = {}
    for item in notif_8p.get("notificationItems", []):
        if item.get("eventType") != "QOS_MONITORING":
            continue
        if item.get("ratType") not in ("NR", "EUTRA"):
            continue
        supi = item.get("supi")
        m = item.get("qosMonitoringMeasurement", {})
        qos[supi] = {
            "ueIp": item.get("ueIpv4Addr"),
            "dlDelay_ms": m.get("dlPacketDelay"),
            "ulDelay_ms": m.get("ulPacketDelay"),
            "dlThr_mbps": _parse_bitrate_mbps(m.get("dlAveThroughput")),
            "ulThr_mbps": _parse_bitrate_mbps(m.get("ulAveThroughput")),
            "plr_pct": m.get("_packetLossRate"),
        }
    return qos


def extract_perf_budget_per_ueip(*notifs):
    """9p / 10p_a PERF_DATA -> {ueIp: max acceptable QoS budgets}."""
    budget = {}
    for notif in notifs:
        for ev in notif.get("eventNotifs", []):
            if ev.get("event") != "PERF_DATA":
                continue
            for info in ev.get("perfDataInfos", []):
                ueip = info.get("ueIpAddr", {}).get("ipv4Addr")
                pd = info.get("perfData", {})
                if not ueip:
                    continue
                budget.setdefault(ueip, {}).update({
                    "maxPdbDl_ms": pd.get("maxPdbDl"),
                    "maxPdbUl_ms": pd.get("maxPdbUl"),
                    "maxPlrDl_pct": pd.get("maxPlrDl"),
                    "maxPlrUl_pct": pd.get("maxPlrUl"),
                })
    return budget


def extract_non3gpp_background(notif_12p):
    """12p_c USER_DATA_USAGE_MEASURES (any-to-any) -> aggregate Non-3GPP load."""
    dl_avg = ul_avg = dl_peak = ul_peak = 0.0
    for item in notif_12p.get("notificationItems", []):
        if item.get("eventType") != "USER_DATA_USAGE_MEASURES":
            continue
        for m in item.get("userDataUsageMeasurements", []):
            tps = m.get("throughputStatisticsMeasurement", {})
            dl_avg += _parse_bitrate_mbps(tps.get("dlAverageThroughput")) or 0.0
            dl_peak += _parse_bitrate_mbps(tps.get("dlPeakThroughput")) or 0.0
            ul_avg += _parse_bitrate_mbps(tps.get("ulAverageThroughput")) or 0.0
            ul_peak += _parse_bitrate_mbps(tps.get("ulPeakThroughput")) or 0.0
    return {"dlAvg_mbps": dl_avg, "ulAvg_mbps": ul_avg,
            "dlPeak_mbps": dl_peak, "ulPeak_mbps": ul_peak}


def extract_gnb_state(notif_13p):
    """13p_d _POWER_ENERGY_CONSUMPTION + _RF_SIGNAL -> per-gNB state + UEs."""
    gnbs = {}
    for ev in notif_13p.get("eventNotifs", []):
        if ev.get("event") == "_POWER_ENERGY_CONSUMPTION":
            for info in ev.get("_powerEnergyConsInfos", []):
                gid = _gnb_id_value(info.get("_loc", {}))
                if gid is None:
                    continue
                d = info.get("_powerEnergyConsData", {})
                gnbs.setdefault(gid, {"servedUes": {}}).update({
                    "powerState": d.get("_powerState"),
                    "power_mw": _parse_power_mw(d.get("_power")),
                    "peakPower_mw": _parse_power_mw(d.get("_peakPower")),
                    "globalGnbId": _gnb_global_id(info.get("_loc", {})),
                })
        elif ev.get("event") == "_RF_SIGNAL":
            for info in ev.get("_rfSignalInfos", []):
                gid = _gnb_id_value(info.get("_loc", {}))
                if gid is None:
                    continue
                supi = info.get("_supi")
                rf = (info.get("_rfSignalData", {}).get("_refSignalMeasurements") or [{}])[0]
                gnbs.setdefault(gid, {"servedUes": {}})
                # If _RF_SIGNAL arrives before _POWER, capture globalGnbId here too.
                if "globalGnbId" not in gnbs[gid]:
                    gnbs[gid]["globalGnbId"] = _gnb_global_id(info.get("_loc", {}))
                gnbs[gid].setdefault("servedUes", {})[supi] = {
                    "ueIp": info.get("_ueIpAddr", {}).get("ipv4Addr"),
                    "connectivity": rf.get("_connectivity"),
                    "rsrp": rf.get("_rsrp"),
                    "sinr": rf.get("_sinr"),
                }
    return gnbs


def decide_cell_power_state(
    notif_8p, notif_9p, notif_10p_a, notif_11p_b, notif_12p_c, notif_13p_d,
    thresholds=None,
):
    """Aggregate 6 notifications -> per-gNB decision dict.

    Returns: {
        gNbId: {
            decision: "DEEP_SLEEP" | "ACTIVE",
            current_state: str,
            reasons_active: [str, ...],
            globalGnbId: {plmnId, gNbId, nid},
            metrics: {...},
        }
    }
    Decision is ACTIVE if any of R1-R5 fails; DEEP_SLEEP otherwise.

      R1 primary cell never sleeps
      R2 must be currently ACTIVE
      R3 power saving must be worthwhile
      R4 Non-3GPP must be able to absorb the offload
      R5 each UE must have QoS headroom
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    qos = extract_qos_per_supi(notif_8p)
    perf_budget = extract_perf_budget_per_ueip(notif_9p, notif_10p_a)
    non3gpp = extract_non3gpp_background(notif_12p_c)
    gnbs = extract_gnb_state(notif_13p_d)

    out = {}
    for gid, gnb in gnbs.items():
        reasons_active = []

        if gid in th["primary_gnb_ids"]:
            reasons_active.append(f"gNb {gid} is primary access (never sleep)")

        if gnb.get("powerState") != "ACTIVE":
            reasons_active.append(f"current powerState={gnb.get('powerState')}")

        pwr = gnb.get("power_mw") or 0.0
        if pwr < th["min_gnb_power_for_sleep_mw"]:
            reasons_active.append(
                f"power={pwr}mW < {th['min_gnb_power_for_sleep_mw']}mW (saving trivial)"
            )

        ue_dl = sum((qos.get(s, {}).get("dlThr_mbps") or 0.0)
                    for s in (gnb.get("servedUes") or {}))
        ue_ul = sum((qos.get(s, {}).get("ulThr_mbps") or 0.0)
                    for s in (gnb.get("servedUes") or {}))
        spare_dl = th["non3gpp_peak_dl_mbps"] - non3gpp["dlAvg_mbps"]
        spare_ul = th["non3gpp_peak_ul_mbps"] - non3gpp["ulAvg_mbps"]

        if ue_dl > spare_dl:
            reasons_active.append(
                f"DL offload infeasible: {ue_dl:.1f} > spare {spare_dl:.1f} Mbps"
            )
        if ue_ul > spare_ul:
            reasons_active.append(
                f"UL offload infeasible: {ue_ul:.1f} > spare {spare_ul:.1f} Mbps"
            )

        for supi in (gnb.get("servedUes") or {}):
            ue_q = qos.get(supi)
            if not ue_q:
                reasons_active.append(f"UE {supi}: no QoS data")
                continue
            ueip = ue_q.get("ueIp")
            budget = perf_budget.get(ueip, {})
            if (ue_q.get("dlDelay_ms") is not None and budget.get("maxPdbDl_ms") is not None
                and ue_q["dlDelay_ms"] > budget["maxPdbDl_ms"] - th["qos_delay_headroom_ms"]):
                reasons_active.append(
                    f"UE {supi}: dlDelay {ue_q['dlDelay_ms']}ms > "
                    f"maxPdbDl {budget['maxPdbDl_ms']}ms - {th['qos_delay_headroom_ms']}ms headroom"
                )
            if (ue_q.get("plr_pct") is not None and budget.get("maxPlrDl_pct") is not None
                and ue_q["plr_pct"] > budget["maxPlrDl_pct"] - th["qos_plr_headroom_pct"]):
                reasons_active.append(
                    f"UE {supi}: PLR {ue_q['plr_pct']}% > "
                    f"maxPlrDl {budget['maxPlrDl_pct']}% - {th['qos_plr_headroom_pct']}% headroom"
                )

        decision = "ACTIVE" if reasons_active else "DEEP_SLEEP"
        out[gid] = {
            "decision": decision,
            "current_state": gnb.get("powerState"),
            "reasons_active": reasons_active,
            "globalGnbId": gnb.get("globalGnbId"),
            "metrics": {
                "ue_dl_total_mbps": ue_dl,
                "ue_ul_total_mbps": ue_ul,
                "non3gpp_spare_dl_mbps": spare_dl,
                "non3gpp_spare_ul_mbps": spare_ul,
                "gnb_power_mw": pwr,
            },
        }
    return out


def build_cell_power_ctrl_notification(
    decisions,
    decision_time_iso,
    correlation_id,
    subscription_id,
    confidence=0.95,
    opt_score=1.0,
):
    """Wrap decisions into the 15f
    (NncofEventsSubscriptionNotification NCOF -> RICF) JSON shape.

    Returns a list (top-level shape of 15f). Empty list if no gNB needs a
    state change.
    """
    decision_dt = datetime.fromisoformat(decision_time_iso)
    stop_dt = decision_dt + SUBSCRIPTION_DURATION
    start_iso = decision_dt.isoformat(timespec="seconds")
    stop_iso = stop_dt.isoformat(timespec="seconds")

    param_sets = []
    seq = 0
    for gid, d in decisions.items():
        if d["decision"] == d.get("current_state"):
            continue
        seq += 1
        preset = CELL_POWER_PARAM_PRESETS.get(d["decision"], {})
        param_sets.append({
            "_ctrlTimeWin": {"startTime": start_iso, "stopTime": stop_iso},
            "_validityPeriod": {"startTime": start_iso, "stopTime": stop_iso},
            "_spatialValidity": {"gRanNodeIds": [d["globalGnbId"]]},
            "_ratTypes": ["NR"],
            "_cellPowerParamSet": {
                "_paramSetId": f"CELL_POWER_PARAM_SET-{start_iso}_{seq}",
                "_cellPowerState": d["decision"],
                **preset,
            },
        })

    if not param_sets:
        return []

    return [{
        "subscriptionId": subscription_id,
        "notifCorrId": correlation_id,
        "resourceUri": f"{NCOF_RESOURCE_BASE}/{subscription_id}",
        "transEvents": ["_CELL_POWER_CTRL"],
        "eventNotifications": [{
            "event": "_CELL_POWER_CTRL",
            "timeStampGen": start_iso,
            "start": start_iso,
            "expiry": stop_iso,
            "anaMetaInfo": {
                "numSamples": 1,
                "dataWindow": {"startTime": start_iso, "stopTime": stop_iso},
                "dataStatProps": ["NO_OUTLIERS"],
                "strategy": "GRADIENT",
                "_optScore": opt_score,
                "nfIds": [RICF_NF_ID],
            },
            "pauseInd": False,
            "resumeInd": False,
            "cancelAccuInd": False,
            "_ctrlEventInd": True,
            "_cellPowerCtrlOptInfos": [{
                "_cellPowerCtrlInfo": {
                    "_tsStart": start_iso,
                    "_tsDuration": str(int(SUBSCRIPTION_DURATION.total_seconds())),
                    "_ratFreq": {
                        "allFreq": True,
                        "allRat": False,
                        "ratType": "NR",
                    },
                    "_confidence": confidence,
                    "_optScore": opt_score,
                    "_cellPowerParamSets": param_sets,
                },
            }],
        }],
    }]


def _derive_meta(notif_13p):
    """Pull the most recent event timestamp from 13p_d, derive sub/corr IDs."""
    ts = None
    for ev in notif_13p.get("eventNotifs", []):
        ts = ev.get("timestamp") or ev.get("timeStamp")
        if ts:
            break
    if ts is None:
        notif_id = notif_13p.get("notifId", "")
        m = re.search(r"\d{4}-\d{2}-\d{2}T[\d:]+(\+\d{2}:\d{2}|Z)", notif_id)
        if m:
            ts = m.group(0)
    if ts is None:
        ts = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    return ts, f"NOTIFICATION_{ts}_1", f"SUBSCRIPTION_{ts}_1"


def main():
    base = Path(__file__).parent.parent / "ncof_json"

    def load(fn):
        return json.loads((base / fn).read_text())

    n_8p = load("8p_NotificationData_from_UPF_to_NCOF_v1.0.json")
    n_9p = load("9p_NefEventExposureNotif_from_AF_to_NCOF_v1.0.json")
    n_10p = load("10p_a_NefEventExposureNotif_from_RICF_to_NCOF_v1.0.json")
    n_11p = load("11p_b_NefEventExposureNotif_from_AF_to_NCOF_v1.0.json")
    n_12p = load("12p_c_NotificationData_from_UPF_to_NCOF_v1.0.json")
    n_13p = load("13p_d_NncofEventsSubscriptionNotification_from RICF_to_NCOF_v1.0.json")

    decisions = decide_cell_power_state(n_8p, n_9p, n_10p, n_11p, n_12p, n_13p)
    ts, corr, sub = _derive_meta(n_13p)
    notification = build_cell_power_ctrl_notification(
        decisions, decision_time_iso=ts, correlation_id=corr, subscription_id=sub,
    )
    print(json.dumps(notification, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
