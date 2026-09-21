"""The single decision call shared by journey legs and wild-encounter battle turns."""
import json
import math
import time

from modules.context import context


def ask_jev(session, state, criteria, instructions, labels, state_field="battle", default_baseline=None, event_extra=None, message_prefix=""):
    """Ask Jev to pick one of `criteria` (key -> description). Shared by battle turns and journey legs."""
    if session.status["decisions"] >= session.config["max_decisions"]:
        raise RuntimeError("Decision limit reached. Run stopped.")
    session.status.update(phase="thinking", message="Jev is choosing an action", **{state_field: state})
    started = time.monotonic()
    request = {"model": "jev-1.13.0", "state": state, "questions": {"action": {
        "type": "choice", "instructions": instructions, "criteria": criteria}}}
    if session.config["agent"] == "baseline":
        action = default_baseline() if default_baseline else next(iter(criteria))
        response = None
    else:
        import requests
        response = None
        for attempt in range(3):
            session.check_stop()
            reply = requests.post("https://api.typesafe.ai/v1/systemone", json=request,
                headers={"Authorization": f"Bearer {session.key}"}, timeout=45)
            if reply.status_code in (429, 500, 502, 503, 504, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if reply.status_code != 200:
                raise RuntimeError(f"TypeSafe HTTP {reply.status_code}. Run stopped without fallback.")
            response = reply.json()
            break
        answer = response.get("answers", {}).get("action", {})
        action = answer.get("choice")
        if answer.get("type") != "choice" or action not in criteria:
            raise RuntimeError("Jev returned an invalid action.")
        probabilities = answer.get("probabilities", {})
        confidence = answer.get("confidence")
        if (not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1
            or set(probabilities) != set(criteria)
            or any(not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values())
            or abs(sum(probabilities.values()) - 1) > 0.02):
                raise RuntimeError("Jev returned invalid probabilities or confidence.")
        session.metrics["api_calls"] += 1
        session.metrics["input_tokens"] += response.get("usage", {}).get("input_tokens", 0)
        session.metrics["output_tokens"] += response.get("usage", {}).get("output_tokens", 0)
        session.metrics["estimated_cost_usd"] = session.metrics["input_tokens"] * 0.042 / 1000000
    session.check_stop()
    session.status["decisions"] += 1
    record = {"decision": session.status["decisions"], "frame": context.frame,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "video_time": session.frames / 59.7275, "labels": labels,
        "request": request, "response": response, "action": action}
    with (session.run_dir / "decisions.jsonl").open("a") as file:
        file.write(json.dumps(record) + "\n")
    session.status.update(phase="playing", message=f"{message_prefix}{labels[action]}", last_decision=record)
    session.metrics["last_latency_ms"] = record["latency_ms"]
    session.metrics["latency_ms"] += record["latency_ms"]
    event = {"message": session.status["message"], "decision": record["decision"], **(event_extra or {})}
    session.status["events"] = (session.status.get("events", []) + [event])[-20:]
    print(f"Decision {session.status['decisions']}: {message_prefix}{labels[action]}", flush=True)
    return action
