"""Exercise both protocol-3 roles with synthetic tones, without audio devices."""

import argparse
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    model = parser.add_mutually_exclusive_group(required=True)
    model.add_argument("--whisper-model", type=Path)
    model.add_argument("--reazon-model", type=Path)
    parser.add_argument("--device", choices=["auto", "cpu", "gpu"], default="auto")
    args = parser.parse_args()
    command = [str(args.binary.resolve()), "--shared"]
    if args.whisper_model:
        command += ["--whisper-model", str(args.whisper_model.resolve()),
                    "--inference-device", args.device, "--language", "ja"]
    else:
        command += ["--reazon-model", str(args.reazon_model.resolve())]
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
           if key in os.environ}
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
    replies = queue.Queue()

    def read():
        try:
            for line in process.stdout:
                replies.put(json.loads(line))
        finally:
            replies.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    sequence = 0
    responses = {}
    segments = []

    def receive():
        reply = replies.get(timeout=180)
        if reply is None:
            raise RuntimeError("worker exited before completing the request")
        if reply["type"] == "error":
            raise RuntimeError("worker returned " + reply["code"])
        return reply

    def send(op, **fields):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({"id": sequence, "command": {"op": op, **fields}}) + "\n")
        process.stdin.flush()
        return sequence

    def wait(identifier):
        deadline = time.monotonic() + 180
        while identifier not in responses:
            if time.monotonic() >= deadline:
                raise TimeoutError("worker response timeout")
            reply = receive()
            if reply.get("id") is not None:
                assert reply["id"] not in responses
                responses[reply["id"]] = reply
            elif reply["type"] == "segment":
                segments.append(reply)
            else:
                assert reply["type"] == "lag"
        return responses.pop(identifier)

    def call(op, **fields):
        return wait(send(op, **fields))

    # Force one second of tone through VAD and the speech gate to test inference.
    frames = [[int(1000 * math.sin(2 * math.pi * 220 * (frame * 480 + n) / 16000))
               for n in range(480)] for frame in range(34)]
    try:
        ready = receive()
        assert ready["type"] == "ready" and ready["protocol"] == 3
        started = time.perf_counter()
        prepared = call("prepare")
        assert prepared["type"] == "prepared"
        print(json.dumps({"phase": "prepare", "ms": round((time.perf_counter() - started) * 1000, 2),
                          "device": prepared.get("execution_device")}), flush=True)
        assert not segments
        assert call("configure", vad_threshold=0.0, silence_seconds=0.4,
                    min_voiced_ms=0, min_voiced_ratio=0.0,
                    min_rms_dbfs=-120.0)["type"] == "configured"
        for generation in (1, 2):
            segments.clear()
            for role in ("self", "other"):
                assert call("reset", role=role)["type"] == "reset"
            for pcm in frames:
                assert call("audio", role="self", pcm=pcm)["segment"] is None
            started = time.perf_counter()
            self_finish = send("finish", role="self")
            assert call("audio", role="other", pcm=frames[0])["segment"] is None
            first_ack_ms = round((time.perf_counter() - started) * 1000, 2)
            ack_before_finish = self_finish not in responses
            for pcm in frames[1:]:
                assert call("audio", role="other", pcm=pcm)["segment"] is None
            other_finish = send("finish", role="other")
            for identifier in (self_finish, other_finish):
                finished = wait(identifier)
                assert finished["type"] == "finished" and finished["segment"] is None
            assert len(segments) == 2
            assert {event["role"] for event in segments} == {"user", "other"}
            for event in segments:
                segment = event["segment"]
                assert segment["generation"] == generation
                assert segment["start_sample"] == 0
                assert segment["end_sample"] == 34 * 480
                assert segment["recognition"]["status"] == "recognized"
            print(json.dumps({"phase": "two_inputs", "generation": generation,
                              "total_ms": round((time.perf_counter() - started) * 1000, 2),
                              "other_first_ack_ms": first_ack_ms,
                              "ack_before_self_finish": ack_before_finish,
                              "segments": len(segments)}), flush=True)
            assert call("prepare")["load_ms"] == 0.0
        assert call("shutdown")["type"] == "stopped"
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        reader.join(timeout=10)
        process.stdin.close()
        process.stdout.close()


if __name__ == "__main__":
    main()
